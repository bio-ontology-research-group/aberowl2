# Self-hosting AberOWL 2

Goal: let anyone run their **own** AberOWL 2 over their **own** set of ontologies
with a single command, on one host. This is a second delivery mode alongside the
public hosted service (aber-owl.net):

- **Hosted repository** — the curated public corpus at aber-owl.net.
- **Self-hosted** — you deploy a private instance over your own K ontologies, so
  you do not depend on the public service and **privacy-sensitive ontologies never
  leave your infrastructure** (your LLM agent reasons over them locally via the
  built-in MCP server, with no external calls).

> Status: **design / WIP** (this PR). Implementation lands next; checklist below.

## Why single-host is simple
The building blocks already exist:
- `central_server/docker-compose.yml` — central-server + redis + `elasticsearch:7.17.10` on `aberowl-net`.
- `deploy/docker-compose.worker.yml` — a worker (Groovy/OWLAPI + ELK) that reaches ES by
  **container name** (`CENTRAL_ES_URL=http://elasticsearch:9200`) on `aberowl-net`.

When central, ES and the worker are co-located on one host they share `aberowl-net`,
so docker DNS resolves the names and **none of the cross-host IP wiring the production
cluster needed applies here**. That is the whole reason a turnkey single-host path is
feasible with little new code.

## Target UX (what we are building)
```bash
# 1. drop your ontologies + a config in ./ontologies/
#    ontologies/
#      ontologies.json        # [{"id":"MYONT","path":"/data/myont/myont.owl","reasoner":"elk"}, ...]
#      myont/myont.owl
# 2. one command
docker compose -f deploy/docker-compose.selfhost.yml up
# 3. use it
#    web/API  -> http://localhost:8000
#    MCP      -> http://localhost:8000/mcp/ontology/mcp  (point your agent here)
```

## Implementation checklist
- [ ] `deploy/docker-compose.selfhost.yml` — one file bringing up redis + ES + central-server
      + one worker on `aberowl-net`, worker `ONTOLOGY_PATH=/data/ontologies.json`,
      ontologies bind-mounted read-only.
- [ ] **Bake in the prod-deploy lessons** so a first-time user does not hit them:
  - central started with its env loaded (`env_file:` in the compose, not a bare `-f`);
  - `ONTOLOGIES_HOST_PATH=/data` on central so the reindex `owlPath` matches the worker mount;
  - auto-generate `ABEROWL_SECRET_KEY` + `ADMIN_PASSWORD` on first run if absent (an entrypoint), so there is no manual `.env` step and no `changeme` default;
  - build the worker image with BuildKit (the legacy builder drops the Grape cache).
- [ ] Registration + indexing on `up`: worker registers with central by container-name URL,
      central indexes each ontology into ES (an init step, or `ABEROWL_REGISTER=true`).
- [ ] A `docker-compose.selfhost.override.yml` or env for optional nginx + friendly `/mcp` route.
- [ ] Quickstart in `README.md` (this UX) + a tiny example `ontologies/` (e.g. BFO) to `up` out of the box.
- [ ] Verify from a clean host: empty machine -> `up` -> DL query + search + all 10 MCP tools work over the example ontology.

## Notes
- Multi-worker packing (`plan_workers.py`) is for large corpora; a self-host with K
  ontologies runs one worker by default, and can scale to a few by copying the worker service.
- The public deploy path (`deploy/deploy.sh`, cross-host) is unchanged; this is additive.
