# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AberOWL 2 is a distributed ontology query system for biological/biomedical ontologies. Each ontology runs in its own Docker container stack (Virtuoso + Groovy API + Elasticsearch + Nginx + LLM). A central server acts as a registry and query aggregator across all instances.

## Architecture

**Per-ontology Docker stack** (docker-compose.yml):
- **Virtuoso**: RDF/SPARQL store, loads OWL files
- **ontology-api**: Groovy/OWLAPI-based reasoning engine (Jetty on port 8080), managed by Python (`docker/scripts/api_server.py`)
- **Elasticsearch 7.x**: Full-text indexing of ontology terms
- **indexer**: One-shot Groovy job that indexes terms into ES
- **nginx**: Reverse proxy exposing everything on a single port (`/api/` → ontology-api, `/virtuoso/` → Virtuoso SPARQL, `/llm` → LLM service)
- **llm**: FastAPI + CAMEL framework for natural language → DL query parsing (uses OpenRouter/DeepSeek)

**Central server** (`central_server/`):
- FastAPI app with Redis backend for server registry
- Aggregates queries across registered ontology instances
- MCP server (`central_server/mcp_server.py`) for LLM agent integration
- Run with: `cd central_server && docker compose up -d`

**Key code layout**:
- `aberowlapi/OntologyServer.groovy` — Jetty server hosting API servlets
- `aberowlapi/api/*.groovy` — Individual API endpoints (runQuery, findRoot, reloadOntology, etc.)
- `aberowlapi/src/*.groovy` — Core logic: RequestManager (ontology lifecycle), QueryEngine, QueryParser, ShortFormProviders
- `aberowlapi/server_manager.py` — Python process that launches the Groovy server
- `aberowlapi/virtuoso_manager.py` — Virtuoso SQL client
- `agents/query_parser.py` — LLM query parser (FastAPI)
- `central_server/app/main.py` — Central server FastAPI app

## Common Commands

### Start an ontology instance (foreground)
```bash
./start_docker.sh data/ontology.owl 8080
```

### Start an ontology instance (detached, with options)
```bash
./reload_docker.sh -d data/ontology.owl 8080
./reload_docker.sh --build -d data/ontology.owl 8080              # force rebuild
./reload_docker.sh --register http://localhost:8000 -d data/go.owl 8080  # with central server registration
```

### Stop an instance
```bash
./reload_docker.sh --stop 8080
```

### View logs
```bash
docker compose -p aberowl_8080 logs -f ontology-api
```

### Local development (without Docker)
```bash
conda env create -f environment.yml
conda activate aberowl2
python manage.py runontapi -o data/pizza.owl   # starts Groovy server on port 8080
```

### Central server
```bash
cd central_server && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Run tests
```bash
pytest tests/                        # all tests
pytest tests/aberowlapi/             # ontology API tests
pytest tests/aberowlapi/test_health.py -v  # single test file
```

Tests expect a running instance at `http://localhost:88/api` (Docker). They use `gevent.monkey.patch_all()` — the `import requests` must come after the gevent monkey-patch.

## Key Technical Details

- **Groovy servlets**: Each `aberowlapi/api/*.groovy` file is a servlet with implicit `request`/`response` objects. Parameters extracted via `Util.extractParams(request)`. All servlets share an application-scoped `RequestManager` via `application.manager`.
- **Java/Groovy dependencies**: Managed via `@Grab` annotations (Grapes). Key deps: OWLAPI 4.5.29, ELK 0.4.3 reasoner, Jetty 9.4.7, RDF4J 2.5.4.
- **Port isolation**: Each instance uses port-specific ES indices (`ontology_index_{PORT}`, `class_index_{PORT}`) and a unique Docker Compose project name (`aberowl_${PORT}`).
- **Docker network**: All instances share the `aberowl-net` external network for cross-container communication.
- **Env files**: `reload_docker.sh` generates `env_files/aberowl_{PORT}.env` for reproducible configuration.
- **Security**: `reloadOntology.groovy` validates file paths (must be under `/data/`). Each ontology gets an auto-generated secret key for registration updates.
