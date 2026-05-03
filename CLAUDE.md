# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AberOWL 2 is a distributed ontology query system for biological/biomedical ontologies. A **central server** (Elasticsearch + Redis + FastAPI) owns shared infrastructure and the registry. **Worker containers** (one Groovy/OWLAPI process per container) each host **one or many ontologies** in-memory and serve DL reasoning queries. Workers register themselves with the central server; the central server dispatches queries to the correct worker by `ontologyId`.

## Architecture

**Central server stack** (`central_server/docker-compose.yml`):
- **FastAPI app** (`app/main.py`): registry, query aggregator, source-sync (OBO Foundry + BioPortal daily), update pipeline, catalogue API, SPARQL rewriter (`/api/sparql`). Exposes port 8000.
- **Elasticsearch 7.x**: shared full-text index (indices `ontology_index` and `owl_class_index`). Boosted `dis_max` search replaces the old scatter-gather across workers.
- **Redis**: ontology registry, task queue, rate-limit state.
- **MCP server**: `mcp_ontology_server.py` (port 8766, 7 tools — including `rewrite_sparql`). Built on the official `mcp` SDK, supports stdio + streamable HTTP. Auto-spawned by `app/main.py` when `ENABLE_MCP=true`.
- Run with: `cd central_server && docker compose up -d`

The central server does not store triples and does not execute SPARQL. `/api/sparql` rewrites queries containing OWL DL frames (`VALUES ?x { OWL subeq go-plus { 'cell death' } }` or `FILTER OWL(?x, subeq, GO, "...")`) into plain SPARQL with concrete IRIs spliced in; the caller runs the rewritten query against any endpoint they choose (Ontobee, UniProt, Wikidata, …).

**Worker stack** (`docker-compose.yml` at repo root):
- **ontology-api**: Groovy Jetty server (`aberowlapi/OntologyServer.groovy`) managed by `aberowlapi/server_manager.py`. A single `RequestManager` holds a `ConcurrentHashMap<String, …>` of ontologies + reasoners keyed by `ontologyId` — one worker can host many ontologies.
- **nginx**: reverse proxy (`/api/` → ontology-api).
- Three startup modes, selected by the `ONTOLOGY_PATH` env var:
  - **File** (`/data/foo.owl`) — single-ontology (backward compatible).
  - **Directory** (`/data/`) — loads every `.owl` file in the directory; id derived from filename.
  - **JSON config** (`/data/ontologies.json`) — explicit list of ontologies loaded by one worker: `[{"id": "GO", "path": "/data/go.owl", "reasoner": "elk"}, …]`.
- The LLM service (natural-language → DL query) lives in `agents/query_parser.py` (FastAPI + CAMEL + OpenRouter). It is optional and not part of the worker by default.

**Registration & dispatch**:
- In multi-ontology mode each loaded ontology still gets its own registry entry in the central server, but all entries can share the same worker URL. The worker routes internally by `ontologyId`.
- Workers expose runtime management endpoints: `addOntology.groovy`, `removeOntology.groovy`, `listLoadedOntologies.groovy`, `updateOntology.groovy` (hot-swap). These require an `ABEROWL_SECRET_KEY` set on the worker.

**Key code layout**:
- `aberowlapi/OntologyServer.groovy` — Jetty server hosting API servlets; picks single/multi/json mode from argv.
- `aberowlapi/api/*.groovy` — Individual API servlets. Every servlet that needs reasoning accepts an `ontologyId` parameter (auto-resolves when only one ontology is loaded).
- `aberowlapi/src/RequestManager.groovy` — Multi-ontology lifecycle: `loadOntology`, `createReasoner`, `createAllReasoners`, `reloadOntology`, `disposeOntology`, `hasOntology`.
- `aberowlapi/src/ReasonerFactory.groovy` — ELK (default), StructuralReasoner, HermiT.
- `aberowlapi/src/*.groovy` — QueryEngine, QueryParser, ShortFormProviders.
- `aberowlapi/server_manager.py` — Python process launcher; handles optional self-registration with the central server (single-ontology mode).
- `aberowlapi/virtuoso_manager.py` — Virtuoso SQL client.
- `agents/query_parser.py` — LLM query parser (FastAPI).
- `central_server/app/main.py` — FastAPI app: registry, source-sync, daily update check, server list, MCP launcher.
- `central_server/app/intake/` — OBO Foundry, BioPortal, and manual-list intake; writes registry metadata only (does not spin up workers).
- `central_server/app/sparql_expander.py` — Parses SPARQL for `VALUES OWL …` / `FILTER OWL(…)` frames, resolves DL queries against the appropriate worker, and returns the rewritten SPARQL with concrete IRIs spliced in. Per-frame errors (unknown ontology, offline worker, DL parse failure) are surfaced in the response without aborting the whole rewrite.

## Common Commands

### Start the central server (once)
```bash
cd central_server && docker compose up -d
# API:           http://localhost:8000
# MCP ontology:  http://localhost:8766/mcp
# MCP SPARQL:    http://localhost:8767/mcp
```

### Start a single-ontology worker (backward-compatible)
```bash
./reload_docker.sh -d --ontology-id go --register http://localhost:8000 8081
./reload_docker.sh --stop 8081
```

### Start a multi-ontology worker (one container, many ontologies)
Create a JSON config (e.g. `data/ontologies.json`) listing the ontologies the worker should load:
```json
[
  {"id": "pizza", "path": "/data/pizza.owl", "reasoner": "elk"},
  {"id": "go",    "path": "/data/go.owl",    "reasoner": "elk"}
]
```
Start the worker stack with `ONTOLOGY_PATH` pointing at the config:
```bash
ABEROWL_PUBLIC_URL=http://localhost:8081 \
ABEROWL_REGISTER=false \
ONTOLOGIES_HOST_PATH=./data \
ONTOLOGY_PATH=/data/ontologies.json \
NGINX_PORT=8081 \
docker compose -p aberowl_multi up -d --build
```
Then register each ontology with the central server (all entries point at the same worker URL — the worker routes internally by `ontologyId`).

### View worker logs
```bash
docker compose -p aberowl_multi logs -f ontology-api
```

### Local dev (no Docker)
```bash
conda env create -f environment.yml
conda activate aberowl2
python manage.py runontapi -o data/pizza.owl   # Groovy server on port 8080
```

### Run tests
```bash
pytest tests/                           # all tests
pytest tests/test_mcp_servers.py -v     # MCP schema + mocked-HTTP unit tests
pytest tests/aberowlapi/ -v             # worker integration tests (needs running worker)
```

Worker integration tests expect a worker at `http://localhost:88/api` (or whatever `ABEROWL_SERVER_URL` points to). They use `gevent.monkey.patch_all()` — `import requests` must come after the monkey-patch.

End-to-end MCP testing:
```bash
python agents/mcp_test_client.py \
    --ontology http://localhost:8766 \
    --sparql   http://localhost:8767
```

## Key Technical Details

- **Groovy servlets**: each file in `aberowlapi/api/` is a servlet with implicit `request`/`response`. Parameters via `Util.extractParams(request)`. All servlets share the application-scoped `RequestManager` (`application.manager`). Most servlets accept an `ontologyId` param; they auto-resolve when the manager holds exactly one ontology.
- **Java/Groovy deps**: `@Grab` (Grapes). Key: OWLAPI 4.5.29, ELK 0.4.3, HermiT 1.4.5, Jetty 9.4.7, RDF4J 2.5.4.
- **Search path**: `/api/search_all` and `/api/queryNames` query the **central** Elasticsearch directly with a boosted `dis_max` query (obo_id=10000, label=100, synonym=75). The old per-worker scatter-gather is gone.
- **Dynamic management**: worker exposes `addOntology`, `removeOntology`, `listLoadedOntologies`, and `updateOntology` (async hot-swap with `task_id` + `updateStatus` polling). All require `ABEROWL_SECRET_KEY`.
- **Docker network**: workers and central server share the `aberowl-net` external network — create it once with `docker network create aberowl-net`.
- **Env files**: `reload_docker.sh` generates `env_files/aberowl_{PORT}.env` for reproducible configuration.
- **Security**: path traversal checks on `owlPath` (must start with `/data/`); secret-key auth on mutating endpoints; MCP servers are public for now (auth deferred — see `central_server/app/auth.py`).
