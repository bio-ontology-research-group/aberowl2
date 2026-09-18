# Deployment and administration

For one host and a chosen set of ontologies, use [Self-hosting](SELF_HOSTING.md).
The separate central and worker stacks support larger installations. Run the
commands here from the repository root on the deployment host. Keep host access,
proxy configuration and credentials in your private operations configuration.

## Central stack

Install Docker with Compose. Set `ADMIN_PASSWORD` and `ABEROWL_SECRET_KEY` in a
private environment file; workers must use the same shared secret. Set
`ONTOLOGIES_PATH` to the absolute ontology directory. `ADMIN_USER` defaults to
`admin`, `CENTRAL_PORT` to `8000`, and `MCP_ONTOLOGY_PORT` to `8766`.

```bash
docker network create aberowl-net
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env up -d --build
```

Create the network only if it does not already exist. This stack runs FastAPI,
Redis and Elasticsearch. The central image builds the frontend from source.
Named volumes retain the registry, indices and registry seed across rebuilds.
Keep the Compose project name stable to reuse those volumes.

The API is available at `http://localhost:8000`; MCP is at
`http://localhost:8766/mcp`. MCP binds to loopback by default. Set
`MCP_BIND_HOST` to an appropriate interface only when your proxy needs it, and
restrict direct access to the service ports. Configure TLS and public routes in
your own reverse proxy.

## Bulk ontology provisioning

The current bulk launcher requires a checkout at `/data/aberowl`, ontologies
under `/data/aberowl/ontologies`, the central Compose project `deploy`, Docker
network `aberowl-net`, and a worker image named `aberowl-api`. These are fixed
launcher conventions. For an arbitrary installation directory, use the self-host
stack instead.

Set `BIOPORTAL_API_KEY` in the environment before BioPortal downloads or metadata
requests. `download_ontologies.py` reads the retained catalogue in
`central_server/config/beta_ontologies.json`; `download_bioportal.py` discovers
BioPortal submissions. Access restrictions and licensing may prevent some downloads. Inspect
download outcomes before planning workers.

```bash
mkdir -p /data/aberowl/ontologies
uv run deploy/download_ontologies.py /data/aberowl/ontologies
uv run deploy/download_bioportal.py /data/aberowl/ontologies --min-size 20000
uv run deploy/fix_ontology_files.py /data/aberowl/ontologies --dry-run
uv run deploy/fix_ontology_files.py /data/aberowl/ontologies
find /data/aberowl/ontologies -maxdepth 2 -name '*.owl' -printf '%s %p\n' > /tmp/aberowl-sizes.txt
uv run deploy/plan_workers.py --sizes /tmp/aberowl-sizes.txt \
  --existing /data/aberowl/ontologies --out /data/aberowl/ontologies --start 1
docker build -f Dockerfile.api -t aberowl-api .
uv run deploy/launch_workers.py \
  --plan /data/aberowl/ontologies/worker_plan.json --env deploy/.env --port-start 8081
```

Choose unused worker numbers and host ports when extending an installation.
The planner skips ontologies assigned in existing worker configurations. Review
its memory allocations before launch: OWL parsing, import merging and
classification can require much more memory than the file size. The launcher
reserves JVM overhead outside the heap. If a worker exhausts memory, revise its
`ram_gb` allocation and use the launcher's `--recreate` option for that worker.

After workers finish classification, register their ontologies and fetch display
metadata:

```bash
uv run deploy/register_workers.py \
  --plan /data/aberowl/ontologies/worker_plan.json \
  --central http://localhost:8000 --rate-per-min 100 --skip-existing
uv run deploy/fetch_metadata.py /data/aberowl/ontologies --workers 8
```

Registration uses Docker worker names, which the central container resolves on
`aberowl-net`. Check search results after registration and request reindexing
through the administration interface if needed. The download, repair, planning,
launch, registration and metadata helpers also provide separate recovery steps;
`retry_downloads.py` retries failed downloads.

## Verify an installation

```bash
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env ps
curl --fail http://localhost:8000/api/getStats
curl --fail http://localhost:8081/api/health.groovy
python agents/mcp_test_client.py --ontology http://localhost:8766
python scripts/check_api_compat.py http://localhost:8000
```

Use your configured ports. Check classification status, search results and a DL
query for each affected ontology. Compare these checks before and after an
upgrade. A successful health response alone does not establish search or query
correctness. Worker and central logs help diagnose load, indexing and dispatch
failures.

## Back up and upgrade

Before any rebuild or restart, record the deployed commit and container image
IDs. Back up the ontology files, private configuration and the three central
volumes: `deploy_es_data`, `deploy_redis_data` and `deploy_central_config`.
For a consistent filesystem backup, stop the central stack while copying the
volumes; a live Elasticsearch data-directory copy is not a consistent snapshot.
Use Elasticsearch's snapshot facility if the backup must run without stopping it.

For a stopped-volume backup, run from the checkout:

```bash
backup_dir="$PWD/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
git rev-parse HEAD > "$backup_dir/deployed-commit.txt"
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env images > "$backup_dir/images.txt"
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env stop
for volume in deploy_es_data deploy_redis_data deploy_central_config; do
  docker run --rm -v "$volume":/volume:ro -v "$backup_dir":/backup alpine \
    tar czf "/backup/$volume.tar.gz" -C /volume .
done
```

Verify every backup completed, then restart the existing stack with `start` if
not upgrading immediately. Keep backups outside the release archive and protect
them as private data. Record and back up bind-mounted ontology/configuration
files separately.

Update to the reviewed source revision and rebuild only affected services. For
a central-only change:

```bash
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env up -d --build --no-deps central-server
```

This command assumes Redis and Elasticsearch are already running. Restart any
services stopped for backup before applying it. Worker code is bind-mounted by
the bulk launcher; worker code changes require a controlled restart and
classification checks. `rollout_worker.py` provides sequential restart checks;
review its installation assumptions and dry-run output before use.

To roll back code, restore the recorded revision and rebuild the affected
services, or restore the recorded images. To restore data, stop its consumers,
restore verified backups into the matching volumes, then restart and repeat the
installation checks. Keep the old versioned Elasticsearch index when swapping
an alias so an indexing rollback can restore the previous alias target.
`docker compose down -v` removes named volumes and is not an upgrade command.

## Registry maintenance

Per-ontology registration/webhook keys differ from `ABEROWL_SECRET_KEY`.
`scripts/rotate_registry_keys.py` rotates registry keys and synchronizes the
cold-start seed. Back up Redis and the seed first; review self-registration,
webhook consumers and worker-auth fallback before rotation. Run inside the
central container, first in dry-run mode, then with `--apply` when ready:

```bash
docker cp scripts/rotate_registry_keys.py deploy-central-server-1:/tmp/
docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py
```

`scripts/repoint_ontology.py` moves registry entries to a different worker while
preserving their keys. It edits Redis directly. Use its dry-run mode, confirm the
ontology has loaded on the destination, then apply and verify queries through
the central API. Keep these maintenance operations separate from code upgrades.
