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

## How you feed in ontologies (simplest first)

Point `ONTOLOGIES_DIR` at a folder (defaults to the bundled example). The folder can hold:

1. **Bare files** — drop `myont.owl` in; id becomes `myont`, reasoner ELK. Zero config.
2. **Web sources** — a `sources.txt`, one `[id] URL [reasoner]` per line, downloaded on startup.
3. **Full control** — an `ontologies.config.json` (authoritative): `[{"id","path"|"url","reasoner"}]`.

The `ontology-prepare` step turns whichever of these it finds into a single canonical
`ontologies.json` that the worker loads, so the three modes share one code path
(`deploy/selfhost_init.py`, unit-tested in `tests/test_selfhost_init.py`).

## UX
```bash
# defaults to examples/selfhost/ontologies (the pizza ontology) so `up` just works:
docker compose -f deploy/docker-compose.selfhost.yml up
# your own set:
ONTOLOGIES_DIR=./my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
#   web / API -> http://localhost:8000
#   MCP       -> http://localhost:8766/mcp   (agent endpoint)
```

## How it fits together
Six services on an internal `aberowl-net` (container-name DNS, no cross-host IP wiring):
`redis`, `elasticsearch`, `central-server`, one `worker`, and two one-shots —
`ontology-prepare` (download + write `ontologies.json`, before the worker) and
`ontology-register` (register each loaded ontology with central + trigger its index,
after the worker classifies).

## Implementation checklist
- [x] `deploy/docker-compose.selfhost.yml` — redis + ES + central + one worker + two init one-shots
      on `aberowl-net`; worker `ONTOLOGY_PATH=/data/ontologies.json`, ontologies bind-mounted.
- [x] `ONTOLOGIES_HOST_PATH=/data` on central so the reindex `owlPath` matches the worker mount.
- [x] Registration + indexing on `up` via `ontology-register` (container-name URLs, existing
      `/register` + `/admin/.../reindex` endpoints).
- [x] Three input modes (bare files / `sources.txt` URLs / `ontologies.config.json`) in
      `deploy/selfhost_init.py`, with unit tests.
- [x] Quickstart + tiny example (`examples/selfhost/`, ships the pizza ontology) to `up` out of the box.
- [x] Verified on a clean host (2026-07-20): `up` -> worker classifies pizza -> registers -> indexes;
      DL query returns 8 subclasses of Pizza, search returns hits, and all 10 MCP tools list over
      `http://localhost:8766/mcp`. The `ontology-register` step waits for the ES index to populate (and
      re-triggers once if the first async reindex lands empty), so search works the moment `up` settles.
- [x] MCP endpoint published at `http://localhost:8766/mcp` (central binds `0.0.0.0:8766`).
- [ ] Auto-generate `ABEROWL_SECRET_KEY` + `ADMIN_PASSWORD` on first run (currently sensible
      dev defaults; fine for a private single host, but should self-generate).
- [x] Bake the SPA into the central image (multi-stage `central_server/Dockerfile`: a Node stage runs
      `npm ci && npm run build`, the final stage `COPY --from` the built `dist/`), so the web UI is
      served with no local `npm build`. Prod is unaffected — it bind-mounts its own `dist/` over it.
      Verified: `http://localhost:8000` serves the real SPA (title, `#root`, `/assets/*.js` -> 200).
- [ ] Optional nginx + friendly `/mcp` route (`docker-compose.selfhost.override.yml`).
- [ ] Swap `build:` for the published `ferzcam/aberowl-central` + `ferzcam/aberowl-worker` images
      once they exist, so a user pulls instead of building.

## Notes
- Multi-worker packing (`plan_workers.py`) is for large corpora; a self-host with K
  ontologies runs one worker by default, and can scale to a few by copying the worker service.
- The public deploy path (`deploy/deploy.sh`, cross-host) is unchanged; this is additive.
