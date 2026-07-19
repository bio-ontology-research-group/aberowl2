# AberOWL2 Deployment Guide

## Live Instance

**URL:** https://beta.aber-owl.net

## Architecture

```
Internet
   │
   ▼ DNS: beta.aber-owl.net → 87.106.144.182
borg-server (87.106.144.182)
   │  nginx: SSL termination (Let's Encrypt)
   │  proxy_pass → phenomebrowser.net:27004/aberowl-beta/
   │
   ▼ port 27004 (via Imperva CDN)
frontend / frontend1 (10.254.146.242 / 10.254.147.211)
   │  nginx: include /etc/nginx/aberowl-beta.conf
   │  proxy_pass → 10.67.24.207:8000
   │
   ▼ port 8000
onto / cbontsr01 (10.67.24.207, 256 CPUs, 1TB RAM)
   │
   ├── Central Stack (docker compose project "deploy")
   │   ├── central-server  :8000  FastAPI + SPA frontend + SPARQL rewriter
   │   ├── redis                  Registry + API key store
   │   └── elasticsearch          Central class/ontology index
   │
   └── Worker Containers (aberowl-worker-1 through aberowl-worker-14)
       ├── worker-1   :8081  PR (1.4GB, dedicated)
       ├── worker-2   :8082  NCIT (775MB, dedicated)
       ├── worker-3   :8083  CHEBI (774MB, dedicated)
       ├── worker-4   :8084  UPHENO (397MB, dedicated)
       ├── worker-5   :9001  FMA (254MB, dedicated)
       ├── worker-6   :9002  MONDO (232MB, dedicated)
       ├── worker-7   :9003  GO + FBBT + OBA (3 ontologies)
       ├── worker-8   :9004  EFO + MP + UBERON + HP (4 ontologies, 24GB RAM)
       ├── worker-9   :9005  CL + RADLEX + MESH + ... (10 ontologies)
       ├── worker-10  :9006  30 small ontologies
       ├── worker-11  :9011  VTO
       ├── worker-12  :9012  OGG
       ├── worker-13  :9013  CLO + FOODON + VO + ECTO + PHIPO + FLOPO
       └── worker-14  :9014  31 small ontologies
```

All containers share the `aberowl-net` Docker network for inter-container communication.

### MCP server

The central-server container also hosts an MCP (Model Context Protocol)
server as an in-container subprocess, controlled by env flags:

| Port | Server | Default flag | Status |
|------|--------|--------------|--------|
| 8766 | `mcp_ontology_server.py` | `ENABLE_MCP_ONTOLOGY=true` | shipping (7 tools, including `rewrite_sparql`) |

Gated behind `ENABLE_MCP=true` (the parent switch). The MCP port is
**not** published to `0.0.0.0`. The compose file binds it to
`${MCP_BIND_HOST}` (default `127.0.0.1`); for prod we set
`MCP_BIND_HOST=10.67.24.207` so the frontend nginx can reach it, but
nothing else can. Public access is exclusively via the nginx route
`/aberowl-beta/mcp/ontology/` — see
`deploy/nginx/frontend-aberowl-beta.conf`.

## Server Access

| Server | SSH | User | Notes |
|--------|-----|------|-------|
| onto (cbontsr01) | `ssh onto` | zhapacfp | Main deployment server. SSH key auth. |
| frontend | See borg-infrastructure AGENTS.md | a-hohndor | Needs `sudo rootsh` for nginx changes. |
| frontend1 | See borg-infrastructure AGENTS.md | a-hohndor | Same as frontend. |
| borg-server | `ssh borg-server` | leechuck | Needs `sudo` for nginx. User `root` for direct access via `ssh root@borg-server`. |

## File Layout on onto (/data/aberowl/)

```
/data/aberowl/
├── deploy/
│   ├── docker-compose.central.yml   # Central stack definition
│   ├── docker-compose.worker.yml    # Worker template (not used directly)
│   ├── .env                         # Secrets (ADMIN_PASSWORD, ABEROWL_SECRET_KEY)
│   ├── deploy.sh                    # Deployment script
│   ├── download_ontologies.py       # OBO Foundry downloader
│   ├── download_bioportal.py        # BioPortal downloader
│   ├── download_extra.py            # Extra ontologies (direct URLs)
│   └── nginx/                       # Nginx config templates
├── central_server/                  # Central server code (FastAPI)
│   ├── app/                         # Application code (main.py, etc.)
│   ├── Dockerfile                   # Central server Docker image
│   └── frontend/                    # React SPA source (built to app/static/dist/)
├── aberowlapi/                      # Groovy API code (bind-mounted into workers)
│   ├── OntologyServer.groovy        # Multi-ontology Jetty server
│   ├── src/                         # RequestManager, ReasonerFactory, etc.
│   └── api/                         # Servlet endpoints
├── docker/
│   └── scripts/                     # api_server.py, IndexElastic.groovy
├── Dockerfile.api                   # Worker container image (Groovy + OWLAPI)
└── ontologies/                      # Shared OWL files (bind-mounted read-only)
    ├── go/go.owl
    ├── hp/hp.owl
    ├── pizza/pizza.owl
    ├── ...
    ├── worker_1_config.json         # Worker 1 ontology assignment
    ├── worker_2_config.json         # Worker 2 ontology assignment
    └── ...
```

## How Deployment Works

### Central Stack

The central stack is managed by Docker Compose:

```bash
ssh onto
cd /data/aberowl

# Start/restart central stack
docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d

# View logs
docker logs deploy-central-server-1 -f

# Rebuild after code changes
rsync -avz ... onto:/data/aberowl/   # sync code from dev machine
docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d
```

The central server image is built from `central_server/Dockerfile`. The SPA frontend
is pre-built locally (`cd central_server/frontend && npm run build`) and the output
at `app/static/dist/` is bind-mounted into the container.

### Worker Containers

Workers are started individually with `docker run` (not compose), because each has
different memory limits and ontology assignments:

```bash
ssh onto
cd /data/aberowl
set -a; source deploy/.env; set +a

# Start a worker
docker run -d \
    --name aberowl-worker-N \
    --network aberowl-net \
    -e CONTAINER_ID=worker-N \
    -e ABEROWL_SECRET_KEY="${ABEROWL_SECRET_KEY}" \
    -e CENTRAL_ES_URL=http://deploy-elasticsearch-1:9200 \
    -e ELASTICSEARCH_URL=http://deploy-elasticsearch-1:9200 \
    -e ONTOLOGY_PATH=/data/worker_N_config.json \
    -e JAVA_OPTS="-Xmx12g -Xms2g" \
    -v /data/aberowl/ontologies:/data:ro \
    -v /data/aberowl/aberowlapi:/app/aberowlapi:ro \
    -v /data/aberowl/docker/scripts:/scripts:ro \
    -p PORT:8080 \
    --memory=16g \
    --restart unless-stopped \
    aberowl-api \
    python3 /app/api_server.py /data/worker_N_config.json
```

Key points:
- The `aberowl-api` image is built once: `docker build -f Dockerfile.api -t aberowl-api .`
- Ontology files are bind-mounted read-only from `/data/aberowl/ontologies/`
- Groovy code is bind-mounted read-only from `/data/aberowl/aberowlapi/` — code
  changes take effect on container restart without rebuilding the image.
- Each worker reads a JSON config listing its ontologies:
  ```json
  [
    {"id": "go", "path": "/data/go/go.owl", "reasoner": "elk"},
    {"id": "hp", "path": "/data/hp/hp.owl", "reasoner": "elk"}
  ]
  ```

### Registering Ontologies

After a worker starts and classifies its ontologies, each ontology must be
registered with the central server so aggregation endpoints know about it:

```bash
curl -X POST http://localhost:8000/register \
    -H "Content-Type: application/json" \
    -d '{"ontology": "go", "url": "http://aberowl-worker-7:8080"}'
```

The URL uses the Docker container name (resolved via the `aberowl-net` network).
The central server stores the registration in Redis and periodically fetches
metadata (class count, statistics) from each worker.

### Adding a New Ontology

1. Download the OWL file:
   ```bash
   mkdir -p /data/aberowl/ontologies/NEW_ONT
   curl -o /data/aberowl/ontologies/NEW_ONT/new_ont.owl URL
   ```

2. Either add it to an existing worker's config and restart that worker,
   or create a new worker.

3. Register it with the central server.

### Removing an Ontology

1. Remove from the worker config JSON.
2. Restart the worker: `docker restart aberowl-worker-N`
3. Remove registration: `docker exec deploy-redis-1 redis-cli hdel registered_servers ONT_ID`

## Bulk Onboarding (Full BioPortal + OBO Foundry)

End-to-end workflow for pulling the complete OBO Foundry and BioPortal
catalogs onto a fresh deployment. All scripts are in `deploy/` and
expect to run on `onto`.

### 1. Download OBO Foundry + BioPortal

```bash
# OBO Foundry (uses central_server/config/beta_ontologies.json list)
uv run deploy/download_ontologies.py /data/aberowl/ontologies

# BioPortal: dynamic catalog, skips OBO overlap, --min-size rejects
# tiny stubs/error pages. curl --compressed handles gzip-served files.
uv run deploy/download_bioportal.py /data/aberowl/ontologies \
    --workers 6 --min-size 20000 \
    --log /tmp/bp.log --results-json /tmp/bp_results.json
```

**Known failure modes surfaced by `--results-json`:**
- `404 no_latest_submission` (~65 entries): abandoned BP submissions;
  nothing to download. Skip.
- `403 license_restricted` (~11): MEDDRA, SNOMEDCT, ICD10, ICNP,
  ICPC2P, HERO, MDDB, NDDF, NDFRT, RCD, WHO-ART. Requires accepting
  each ontology's license agreement on the BioPortal account page
  (bioontology.org → per-ontology "request access"). Re-run the
  downloader after approval.

### 2. Repair malformed files (REQUIRED before planning)

BioPortal occasionally returns zip archives named `.owl`, or gzip
without a proper `Content-Encoding: gzip` header. Detect and repair
BEFORE the bin-packer measures sizes — otherwise a 12MB zip gets
placed on a low-RAM worker that later OOMs when the 253MB extracted
OWL tries to load.

```bash
uv run deploy/fix_ontology_files.py /data/aberowl/ontologies
```

The script is idempotent — plain XML/text files are left alone. Files
with gzip magic are gunzipped in-place; zip archives are replaced by
their largest `.owl`/`.rdf`/`.ttl`/`.obo` member.

### 3. Plan and launch workers

```bash
# Inventory on-disk ontologies
find /data/aberowl/ontologies -maxdepth 2 -name "*.owl" -printf "%s %p\n" \
    > /tmp/ont_sizes.txt

# Bin-pack by size into worker configs (respects already-assigned
# ontologies in existing worker_*_config.json)
uv run deploy/plan_workers.py \
    --sizes /tmp/ont_sizes.txt \
    --existing /data/aberowl/ontologies \
    --out /data/aberowl/ontologies \
    --start 15

# Launch. Idempotent; --recreate forces stop+rm before launch.
uv run deploy/launch_workers.py \
    --plan /data/aberowl/ontologies/worker_plan.json \
    --env deploy/.env \
    --port-start 9015
```

**Memory sizing — critical gotchas:**

- `OWLOntologyMerger` (inside `RequestManager.loadOntology`) keeps the
  original ontology AND the merged-imports-closure copy in memory at
  the same time. Peak heap during load is ~2× the "parsed" size, which
  itself can be 8-10× the raw OWL file size. Estimate: **40-50× the
  file size for peak load heap** on ontologies with heavy axioms.
- NCBITaxon (1.8 GB raw OWL) needs ~96 GB container / -Xmx77g to make
  it through load+merge+classify. PR (1.4 GB) needs ~24 GB.
- `launch_workers.py` sets `-Xmx = ram_gb - max(4, ram_gb/5)` — a
  naive `-Xmx = ram - 2` gets SIGKILL'd by the kernel (not docker's
  OOM-killer) because JVM non-heap (metaspace, JIT, direct buffers,
  thread stacks) plus kernel page cache exceed the cgroup limit.
- When a worker hits `java.lang.OutOfMemoryError` during load, bump
  its `ram_gb` in `worker_plan.json` and relaunch with `--recreate`.

### 4. Register with the central server

```bash
# Rate-limited (server caps at 120/min; use 100 to be safe).
# --skip-existing avoids secret-key collisions on re-runs.
uv run deploy/register_workers.py \
    --plan /data/aberowl/ontologies/worker_plan.json \
    --central http://localhost:8000 \
    --rate-per-min 100 --skip-existing
```

### 5. Fetch display metadata

Many BioPortal ontologies have a bare `<owl:Ontology>` element with no
`dc:title`/`rdfs:label`. `getStatistics.groovy` reads the sibling
`metadata.json` (written by this step) as fallback, so the frontend
shows real names.

```bash
uv run deploy/fetch_metadata.py /data/aberowl/ontologies --workers 8
```

Each ontology directory gets a `metadata.json` with `title`,
`description`, `home_page`, `documentation`, `license`, `creators`,
etc. OBO Foundry ontologies are resolved against
`purl.obolibrary.org/meta/ontologies.jsonld`; BioPortal ones via
`/ontologies/{acronym}` + `/latest_submission`. The central server's
60-second periodic poll picks up the new metadata automatically — no
worker restart required.

### Full procedure, in order

```bash
uv run deploy/download_ontologies.py /data/aberowl/ontologies
uv run deploy/download_bioportal.py  /data/aberowl/ontologies --min-size 20000
uv run deploy/fix_ontology_files.py  /data/aberowl/ontologies   # REQUIRED before plan
find /data/aberowl/ontologies -maxdepth 2 -name "*.owl" -printf "%s %p\n" > /tmp/s.txt
uv run deploy/plan_workers.py    --sizes /tmp/s.txt --existing /data/aberowl/ontologies --out /data/aberowl/ontologies --start 15
uv run deploy/launch_workers.py  --plan  /data/aberowl/ontologies/worker_plan.json --env deploy/.env --port-start 9015
uv run deploy/register_workers.py --plan /data/aberowl/ontologies/worker_plan.json --central http://localhost:8000 --skip-existing
uv run deploy/fetch_metadata.py   /data/aberowl/ontologies
```

### Known missing ontologies

After a full retry of `download_ontologies.py` and `download_bioportal.py` (with
the 30-min curl timeout), a residual set of registered ontologies never lands
on disk. As of the 2026-05-20 retry, ~243 of 864 registered ontologies have no
usable OWL file. They are excluded from `scripts/plan_distribution.py`'s plan
by default (override with `--include-missing`).

Categories:
- **License-gated (~11)** — `MEDDRA`, `SNOMEDCT`, `ICD10`, `ICNP`, `ICPC2P`,
  `HERO`, `MDDB`, `NDDF`, `NDFRT`, `RCD`, `WHO-ART`. Require per-account
  approval at bioontology.org before download.
- **Abandoned BP submissions (~65)** — BioPortal returns 404
  `no_latest_submission`; nothing to download.
- **Broken OBO purls / parse failures** — e.g. `ero`, `fix` still fail even
  with the long timeout. Need an alternative source URL or local repair.

Each plan run writes `results/missing_ontologies_<date>.md` with the exact
list of skipped ontologies, grouped by likely cause. Revisit periodically as
BP catalog churn or license approvals change.

## Nginx Configuration

### borg-server (/etc/nginx/sites-available/beta.aber-owl.net)

```nginx
server {
    listen 80;
    server_name beta.aber-owl.net;
    # certbot added SSL redirect and 443 block automatically
    location / {
        proxy_pass http://phenomebrowser.net:27004/aberowl-beta/;
        proxy_set_header Host phenomebrowser.net;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

SSL certificate: Let's Encrypt, managed by certbot, auto-renews.

### frontend / frontend1 (/etc/nginx/aberowl-beta.conf)

This file is `include`d in the `phenomebrowser.net` config inside the
`server { listen 27004; }` block (at line 7 of the phenomebrowser.net config):

```nginx
location = /aberowl-beta {
    return 301 /aberowl-beta/;
}

location ^~ /aberowl-beta/ {
    proxy_pass http://10.67.24.207:8000/;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
    proxy_set_header Host               $host;
    proxy_set_header X-Real-IP          $remote_addr;
    proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto  $scheme;
    proxy_set_header X-Forwarded-Host   beta.aber-owl.net;
    proxy_buffering off;
}
```

### Updating nginx

Use the borg-infrastructure deploy pattern:
1. SCP new config file to the server's `/tmp/`
2. PTY SSH + `sudo rootsh` to get root
3. Copy to `/etc/nginx/`, test with `nginx -t`, reload with `systemctl reload nginx`

See `deploy/deploy_nginx.py` for the manual steps.

## Secrets

All secrets are in `/data/aberowl/deploy/.env` on `onto`:

| Variable | Purpose |
|----------|---------|
| `ADMIN_PASSWORD` | HTTP Basic auth for `/admin/*` endpoints |
| `ABEROWL_SECRET_KEY` | Inter-service auth (worker ↔ central) |

Separately, each registry entry carries a per-ontology `secret_key` (a uuid4 in
Redis, used only for `/register` re-registration auth and the `/webhook` update
trigger — **not** the worker `ABEROWL_SECRET_KEY` above). To invalidate those keys
(e.g. after a leak), rotate them — Redis-only, no worker restarts, no downtime:

```bash
docker cp scripts/rotate_registry_keys.py deploy-central-server-1:/tmp/
docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py            # dry-run
docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py --apply    # rotate + resync servers.json
```

Back up the `deploy_redis_data` volume first (see the rollback section above).

## Monitoring

```bash
# All containers
ssh onto "docker ps --filter name=aberowl --format 'table {{.Names}}\t{{.Status}}'"

# Central server health
curl https://beta.aber-owl.net/api/getStats

# Worker health
ssh onto "curl -sf http://localhost:8081/api/health.groovy"

# Central server logs
ssh onto "docker logs deploy-central-server-1 -f"

# Worker logs
ssh onto "docker logs aberowl-worker-1 -f"

# Redis
ssh onto "docker exec deploy-redis-1 redis-cli info keyspace"

# Elasticsearch
ssh onto "docker exec deploy-elasticsearch-1 curl -sf http://localhost:9200/_cluster/health?pretty"
```

## Pre-deployment Regression Test

Run this from your **laptop** before and after any nginx or central-server
change. Save the before output as a baseline and diff against after — any
status code that changed is a regression to investigate.

```bash
# Capture a baseline before making changes:
for u in http://phenomebrowser.net/ http://vsim.phenomebrowser.net/ http://hgupload.phenomebrowser.net/ http://patho.phenomebrowser.net/ http://ddiem.phenomebrowser.net/ http://owas.phenomebrowser.net/ http://ukb.phenomebrowser.net/ http://pavs.phenomebrowser.net/ http://ve.phenomebrowser.net/ http://phenomebrowser.net/rub-al-khali/ https://beta.aber-owl.net/aberowl-beta/; do printf '%-55s %s\n' "$u" "$(curl -sSL -o /dev/null -w '%{http_code}' --max-time 15 "$u")"; done | tee ~/aberowl_smoke_before.txt

# Run the same command after changes, then diff:
for u in http://phenomebrowser.net/ http://vsim.phenomebrowser.net/ http://hgupload.phenomebrowser.net/ http://patho.phenomebrowser.net/ http://ddiem.phenomebrowser.net/ http://owas.phenomebrowser.net/ http://ukb.phenomebrowser.net/ http://pavs.phenomebrowser.net/ http://ve.phenomebrowser.net/ http://phenomebrowser.net/rub-al-khali/ https://beta.aber-owl.net/aberowl-beta/; do printf '%-55s %s\n' "$u" "$(curl -sSL -o /dev/null -w '%{http_code}' --max-time 15 "$u")"; done | tee ~/aberowl_smoke_after.txt

diff ~/aberowl_smoke_before.txt ~/aberowl_smoke_after.txt
```

An empty diff means no regressions. Some routes return 502 or 404 by
design (pre-existing backend issues unrelated to AberOWL) — those are
expected and should remain stable across deployments.

### MCP smoke test (from laptop)

After any central-server or nginx change, verify the MCP endpoint:

```bash
curl -sf https://beta.aber-owl.net/mcp/ontology/mcp -H 'Accept: text/event-stream' -H 'Content-Type: application/json' -X POST -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0"}}}' | head -5
```

Expected: HTTP 200 with `event: message` and `"serverInfo":{"name":"aberowl-ontology",...}` in the body.

## Updating Code

```bash
# From the dev machine (aberowl2 repo root):

# 1. Build frontend
cd central_server/frontend && npm run build && cd ../..

# 2. Sync to server
rsync -avz --exclude node_modules --exclude .venv --exclude .git \
    ./ onto:/data/aberowl/

# 3. Rebuild and restart central server
ssh onto "cd /data/aberowl && docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d"

# 4. Restart workers (if Groovy code changed — no rebuild needed due to bind mount)
ssh onto "docker restart aberowl-worker-{1..14}"
```

### Data preservation across central-stack rebuilds

`docker compose up --build -d` recreates the central-server container
but **keeps the named volumes** (`redis_data`, `es_data`,
`central_config`) and the bind-mounted ontologies in
`/data/aberowl/ontologies`. So Redis registry, Elasticsearch indices,
and downloaded OWL files all survive an upgrade.

What would destroy data:
- `docker compose down -v` (the `-v` removes named volumes — never run this without a backup).
- `rm -rf /data/aberowl/ontologies` or `/var/lib/docker/volumes/deploy_*`.
- Renaming the compose project (`-p` flag) — would create new volumes with the new prefix.

### ALWAYS back up the current state before deploying (rollback safety)

A normal `up --build -d` preserves volumes, but **always capture a rollback
point first** so a bad deploy can be reverted. Run on `onto`, before syncing
new code:

```bash
ssh onto
cd /data/aberowl

# 1. Record the currently-deployed code commit (to roll the code back).
git rev-parse HEAD | tee backups/DEPLOYED_COMMIT_$(date +%Y%m%d_%H%M%S).txt

# 2. Snapshot the data volumes (ES indices, Redis registry, config) to tarballs.
ts=$(date +%Y%m%d_%H%M%S); mkdir -p backups/$ts
for v in deploy_es_data deploy_redis_data deploy_central_config; do
  docker run --rm -v "$v":/vol -v "$(pwd)/backups/$ts":/backup alpine \
    tar czf "/backup/$v.tar.gz" -C /vol .
done
echo "Backup at /data/aberowl/backups/$ts"
```

(Volume names carry the `deploy_` compose-project prefix. `backups/` lives
outside the rsync path, so deploys don't clobber it; prune old ones manually.)

**Rollback:**
- *Code:* `git checkout <recorded-commit>` (or re-sync the old tree) then
  `docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d`.
- *Data:* stop the stack, restore the tarball into the volume, restart, e.g.
  `docker run --rm -v deploy_es_data:/vol -v "$(pwd)/backups/<ts>":/backup alpine sh -c 'rm -rf /vol/* && tar xzf /backup/deploy_es_data.tar.gz -C /vol'`.
- *Schema/reindex changes:* reindex into a **new** versioned index and swap the
  alias, keeping the old index — then rollback is just swapping the alias back,
  with no data loss.

For the MCP rollout specifically, the upgrade sequence is:

```bash
# On dev machine:
git checkout feat/mcp-features
rsync -avz --exclude node_modules --exclude .venv --exclude .git \
    ./ onto:/data/aberowl/

ssh onto
cd /data/aberowl

# Add the new MCP env vars to deploy/.env if not present
grep -q ENABLE_MCP_ONTOLOGY deploy/.env || cat >> deploy/.env <<'EOF'
ENABLE_MCP_ONTOLOGY=true
MCP_BIND_HOST=10.67.24.207
EOF

# Rebuild central stack — volumes are preserved
docker compose -f deploy/docker-compose.central.yml --env-file deploy/.env up --build -d

# Smoke-check (from onto)
curl -sf http://10.67.24.207:8766/mcp -H 'Accept: text/event-stream' \
    -H 'Content-Type: application/json' -X POST -d '{}' | head

# Workers don't need to be touched for an MCP-only upgrade.
```

Then push the nginx route to the frontend (one of `frontend` /
`frontend1`, then the other) — see "Updating nginx" below.

## Port Allocation

| Port | Service |
|------|---------|
| 8000 | Central server (FastAPI) |
| 8081 | Worker 1 (PR) |
| 8082 | Worker 2 (NCIT) |
| 8083 | Worker 3 (CHEBI) |
| 8084 | Worker 4 (UPHENO) |
| 9001 | Worker 5 (FMA) |
| 9002 | Worker 6 (MONDO) |
| 9003 | Worker 7 (GO, FBBT, OBA) |
| 9004 | Worker 8 (EFO, MP, UBERON, HP) |
| 9005 | Worker 9 (10 medium ontologies) |
| 9006 | Worker 10 (30 small ontologies) |
| 9011 | Worker 11 (VTO) |
| 9012 | Worker 12 (OGG) |
| 9013 | Worker 13 (CLO, FOODON, VO, ECTO, PHIPO, FLOPO) |
| 9014 | Worker 14 (31 small ontologies) |

Ports 8080, 8085, 8086 are used by other services on onto (Rub-al-Khali, etc.)
and must not be used for AberOWL workers.

| Port | Service | Binding |
|------|---------|---------|
| 8766 | MCP ontology server | `${MCP_BIND_HOST}` only — never `0.0.0.0` |

## Transitioning to Production (aber-owl.net)

1. Update DNS: point `aber-owl.net` A record to borg-server (87.106.144.182)
   or keep it pointing to frontend/frontend1 and adjust nginx accordingly.
2. Create `/etc/nginx/sites-available/aber-owl.net` on borg-server (copy from beta config, change server_name).
3. Get SSL cert: `certbot --nginx -d aber-owl.net`
4. Scale workers for 800+ ontologies (~40 workers).
5. Decommission old AberOWL (aberowl2 server 10.254.147.137, workers 10.254.146.227/61).
