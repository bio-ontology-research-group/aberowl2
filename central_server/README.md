# AberOWL Central Server

The AberOWL Central Server is a registry and query aggregator for distributed AberOWL ontology servers. It provides a unified interface to query multiple ontology servers simultaneously and implements the FAIR MOD-API specification.

## Features

- **Server Registry**: Ontology servers can register themselves with the central server
- **Unified Querying**: Run DL queries and text searches across all registered ontologies
- **FAIR API**: Implements the MOD-API specification for semantic artefact catalogues
- **MCP Support**: Exposes functionality to LLM agents via Model Context Protocol
- **Auto-discovery**: Automatically fetches and updates metadata from registered servers
- **Web Interface**: User-friendly interface for browsing and querying ontologies

## Quick Start

### Using Docker Compose

The easiest way to run the central server is using Docker Compose:

```bash
cd central_server
docker compose up -d
```

This will start:
- The central server on port 8000
- Redis for data storage
- Elasticsearch for search functionality

### Manual Setup

If running manually, you'll need:
- Python 3.8+
- Redis server
- Elasticsearch (optional, for enhanced search)

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the server:
```bash
cd app
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Model Context Protocol (MCP) Support

The central server ships with one MCP server that lets LLM agents (Claude Desktop, Cursor, custom agents, etc.) query AberOWL programmatically. It uses the official `mcp` Python SDK and supports two transports:

- **stdio** — the client spawns the server as a subprocess (standard for Claude Desktop).
- **streamable HTTP** — the server runs as a long-lived HTTP service at `/mcp` (standard for remote/shared access).

### The server

| Server | File | Default port | Purpose |
|---|---|---|---|
| Ontology | `mcp_ontology_server.py` | 8766 | Browse, search, reason over ontologies, and rewrite SPARQL with embedded OWL DL frames |

### Available tools

**Ontology server (7 tools)**
- `list_ontologies` — all registered ontologies with status and metadata
- `search_classes` — search classes by label/synonym/OBO ID (all ontologies or one)
- `run_dl_query` — Description Logic query in Manchester OWL Syntax (subclass/subeq/superclass/supeq/equivalent)
- `get_class_info` — full annotations/axioms for a class
- `get_ontology_info` — metadata for one ontology
- `browse_hierarchy` — direct subclasses or superclasses of a class
- `rewrite_sparql` — rewrite a SPARQL query containing `VALUES ?x { OWL subeq GO { ... } }` or `FILTER OWL(?x, subeq, GO, "...")` frames to one with concrete IRIs spliced in. AberOWL only rewrites; the caller runs the result against any SPARQL endpoint (Ontobee, UniProt, …).
- `list_sparql_examples` — curated SPARQL+OWL example queries to use as templates.
- `query_sparql` — same rewrite, but also forwards the result to an external SPARQL endpoint (Ontobee by default; pass `endpoint=` for UniProt / Wikidata / DBpedia / etc.) and returns the rows. AberOWL still doesn't host a SPARQL store — this just chains rewrite → POST → format.

### Deploying with Docker (streamable HTTP)

When the central stack is started with `docker compose up`, the MCP server is launched automatically as a subprocess of the central-server container and exposed on port 8766:

```
http://localhost:8766/mcp   # ontology server
```

**Prerequisite — shared Docker network**

The central stack attaches to an external Docker network called `aberowl-net` so worker containers can reach the central services by name. Create it once:

```bash
docker network create aberowl-net
```

**Bringing up the stack**

For local development:

```bash
cd central_server
docker compose up -d
```

For production, a ready-to-use compose file lives at `deploy/docker-compose.central.yml` and is wrapped by `deploy/deploy.sh`, which generates `.env` with random secrets on first run:

```bash
cd deploy
./deploy.sh up           # build & start
./deploy.sh logs         # tail logs
./deploy.sh down         # stop
```

**Environment variables**

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_MCP` | `true` | Launch the MCP server alongside the central FastAPI app. Set `false` to disable. |
| `MCP_ONTOLOGY_PORT` | `8766` | Host port for the ontology MCP server |
| `CENTRAL_SERVER_URL` | `http://localhost:8000` | Base URL the MCP server subprocess calls when servicing tool requests. In Docker the central-server container reaches itself on `localhost:8000`; override only if you run the MCP server on a different host than the central app. |
| `CENTRAL_SERVER_PORT` | `8000` | Host port for the central HTTP API |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `changeme` | Credentials protecting `/admin/*` endpoints |

**Firewall / reverse-proxy checklist**

For public deployments you must expose (or proxy) two ports:

- `8000` — central FastAPI app (registry, search, admin UI, SPARQL rewriter)
- `8766` — ontology MCP server (if you want remote MCP clients to reach it)

If only local/LAN MCP access is needed, bind `8766` to a private interface only.

**Verifying the deployment**

After `docker compose up -d` finishes, confirm all three containers are healthy:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
# aberowl-central-server         Up ... (healthy)
# aberowl-central-elasticsearch  Up ... (healthy)
# aberowl-central-redis          Up ...
```

Quick smoke test of each reachable surface:

```bash
curl -s http://localhost:8000/api/servers                       # registry endpoint — returns JSON list
curl -sI http://localhost:8766/mcp                              # ontology MCP — expect 200/406 (SSE)
curl -s http://localhost:8000/health | python3 -m json.tool     # composite health: es/redis
```

### Running standalone (stdio, for Claude Desktop)

```bash
export CENTRAL_SERVER_URL=http://localhost:8000   # or your deployment URL
python central_server/mcp_ontology_server.py --stdio
```

Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "aberowl-ontology": {
      "command": "python",
      "args": ["/path/to/aberowl2/central_server/mcp_ontology_server.py", "--stdio"],
      "env": {"CENTRAL_SERVER_URL": "http://localhost:8000"}
    }
  }
}
```

### Using the MCP servers from Claude Code (CLI)

The ontology MCP server is exposed over streamable HTTP and can be registered with the Claude Code CLI using `claude mcp add`.

**Option A — public deployment (recommended)**

The production server is live at `https://beta.aber-owl.net/mcp/ontology/mcp`. To use it from any machine with Claude Code installed:

```bash
claude mcp add aberowl-ontology https://beta.aber-owl.net/mcp/ontology/mcp --transport http
```

**Option B — local stack**

With the central stack running (`docker compose up -d` in `central_server/`) the server is reachable at `http://localhost:8766/mcp`:

```bash
claude mcp add aberowl-ontology http://localhost:8766/mcp --transport http
```

**Scope**

By default `claude mcp add` writes to project scope (`.mcp.json` in the current directory, shared via git). Use `--scope user` to register globally for your user across all projects, or `--scope local` for a per-project, per-user override that is not committed.

**Verify**

```bash
claude mcp list        # shows the server, URL, and transport
```

Start (or restart) a Claude Code session and type `/mcp` — the `aberowl-ontology` entry should show `connected` with `9 tools`.

**Remove**

```bash
claude mcp remove aberowl-ontology
```

#### Example prompts to try

DL queries accept either an IRI (`<http://purl.obolibrary.org/obo/GO_0008150>`) or a label in Manchester syntax (`'biological process'`, `'part of' some 'cell'`). Note that `'cell'` returns 0 in current GO releases because GO_0005623 was obsoleted — use `'biological process'` (or any non-deprecated label) for smoke tests.

- *"List the ontologies available via the aberowl tools."* — `list_ontologies`
- *"Search GO for apoptosis using aberowl."* — `search_classes`
- *"Using aberowl, show me the class info for `<http://purl.obolibrary.org/obo/GO_0008150>`."* — `get_class_info`
- *"Using aberowl, get the metadata for the `go` ontology."* — `get_ontology_info`
- *"Using aberowl, give me direct subclasses of `<http://purl.obolibrary.org/obo/GO_0008150>` in GO."* — `browse_hierarchy`
- *"Using aberowl, run a DL query for subclasses of 'biological process' in GO."* — `run_dl_query` (label form)
- *"Using aberowl, show me example SPARQL queries with OWL DL frames."* — `list_sparql_examples`
- *"Using aberowl, rewrite this SPARQL: `SELECT ?c WHERE { VALUES ?c { OWL subeq go-plus { 'cell death' } } }`"* — `rewrite_sparql`
- *"Using aberowl, run this SPARQL against Ontobee: `SELECT ?c ?l WHERE { VALUES ?c { OWL subeq go-plus { 'cell death' } } ?c <http://www.w3.org/2000/01/rdf-schema#label> ?l . } LIMIT 10`"* — `query_sparql`

If Claude Code answers from general knowledge instead of calling a tool, add *"use the aberowl MCP tool"* to the prompt.

### Local end-to-end testing

The repo ships three helpers (at the repo root / `agents/`) that together spin up a local multi-ontology worker, index the ontologies into the central ES, and exercise all MCP tools. Use them to validate changes to the MCP stack end-to-end before shipping.

**Prerequisites**

1. Central stack running: `cd central_server && docker compose up -d`.
2. Two OWL files present at `./data/pizza.owl` and `./data/go.owl`. Pizza is a small tutorial ontology (~100 classes); GO is any recent `go.owl` release.
3. A `data/ontologies.json` file telling the worker which ontologies to load (this file is *not* shipped — create it):

   ```json
   [
     {"id": "pizza", "path": "/data/pizza.owl", "reasoner": "elk"},
     {"id": "go",    "path": "/data/go.owl",    "reasoner": "elk"}
   ]
   ```

4. The `aberowl-net` Docker network exists (created automatically by the central stack, or run `docker network create aberowl-net`).

**Step 1 — start a local multi-ontology worker**

```bash
./start_local_test_worker.sh
```

This builds and runs a single worker container (compose project `aberowl_local_multi`) on port 8081 that hosts every ontology listed in `data/ontologies.json`, waits for classification to finish, and registers each ontology with the central server. Tear it down later with:

```bash
./start_local_test_worker.sh --stop
```

**Step 2 — index the ontologies into central Elasticsearch**

```bash
./reindex_local_test_worker.sh
```

This cleans any stale `registered_servers` entries, triggers `IndexElastic.groovy` on the worker for each ontology, and polls until each task finishes. Expect pizza in ~10 s and GO in ~10 min.

**Step 3 — run the MCP test client**

```bash
python agents/mcp_test_client.py
```

The client connects to the MCP server over streamable HTTP, lists every advertised tool, then calls each tool with realistic arguments.

Flags:

```bash
python agents/mcp_test_client.py \
    --ontology http://localhost:8766
```

**Troubleshooting (read this first if the local stack "doesn't work")**

The single most common failure is **broken docker networking on the host**, not a
bug in the app. Symptoms — any of:

- the central is "Up" but `curl http://localhost:8000/api/servers` hangs / returns nothing;
- the worker image build fails with `No matching distribution found for fastapi`;
- `docker run --rm alpine wget -qO- https://pypi.org` fails even though the *host* has internet;
- `redis ... No route to host` on central startup.

These all mean docker's iptables/NAT rules are corrupted (host↔container and
container→internet are broken). **Fix: restart the docker daemon** — containers
come back on their own (`restart: unless-stopped`):

```bash
sudo systemctl restart docker
# verify networking is restored:
docker run --rm alpine wget -qO- -T8 https://pypi.org >/dev/null && echo "container internet OK"
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/servers   # expect 200
```

To prove it's networking and not the app, hit the API from *inside* the container
(bypasses host port-forwarding). If this is instant while the host call hangs, it's
docker, not the code:

```bash
docker exec aberowl-central-server python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/servers',timeout=8).read()[:40])"
```

Other gotchas:

- **Central hangs and logs `fetch metadata for dummy_bench_… / *.invalid`** — stale
  registry entries (e.g. from a benchmark run) are in Redis, and the periodic refresh
  ties up the event loop trying to reach those dead hosts. Clear them and restart:
  ```bash
  docker exec aberowl-central-redis redis-cli DEL registered_servers
  docker exec aberowl-central-server sh -c 'echo "[]" > /code/app/servers.json'
  docker restart aberowl-central-server
  ```
- **The worker build takes a long time** — the first `start_local_test_worker.sh`
  build resolves the OWLAPI/ELK/Jetty/RDF4J jars via Grapes (hundreds of MB); this
  can take 10–30 min on a cold cache, then it's reused. `go.owl` also takes a few
  minutes to classify. For a fast smoke test, use **pizza only** (it classifies in
  seconds): set `data/ontologies.json` to just pizza and register it directly with
  `curl -X POST http://localhost:8000/register -H 'Content-Type: application/json'
  -d '{"ontology":"pizza","url":"http://aberowl_local_multi-ontology-api-1:8080/"}'`.
- **The Vite dev server** (`cd central_server/frontend && npm run dev`) proxies
  `/api` and `/admin` to `http://localhost:8000` (see `vite.config.ts`), so the
  central stack must be running for the dev UI to load data.

### Connecting over HTTP from Python

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:8766/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("list_ontologies", {})
```

## API Documentation

### Server Registration

Ontology servers register themselves with:

```bash
POST /register
{
  "ontology": "GO",
  "url": "http://my-ontology-server.com"
}
```

Returns a secret key for future updates.

### Query Endpoints

- `GET /api/servers` - List all registered servers
- `GET /api/search_all?query=term` - Search across all ontologies
- `GET /api/dlquery_all?query=expression&type=subclass` - Run DL queries

### FAIR API (MOD-API)

The server implements the MOD-API specification:

- `/records` - Catalog records
- `/artefacts` - Semantic artefacts
- `/artefacts/{id}/distributions` - Artefact distributions
- `/search` - Search functionality

See the web interface documentation for full API details.

## Configuration

### Environment Variables

- `CENTRAL_SERVER_URL`: URL where the central server is accessible (for MCP server)
- `REDIS_URL`: Redis connection URL (default: `redis://redis`)
- `ELASTICSEARCH_URL`: Elasticsearch URL (default: `http://elasticsearch:9200`)

### Configuration Files

- `app/servers.json`: Persistent storage of registered servers
- `app/catalogue_config.json`: Catalogue metadata configuration

## Development

### Running Tests

```bash
pytest tests/
```

### Resetting Data

To clear all registered servers and start fresh:

```bash
python app/main.py --reset
```

Or with Docker:

```bash
docker exec central-server python app/main.py --reset
```

## Architecture

The central server consists of:

1. **FastAPI Application**: Main web server and API
2. **Redis**: Stores server registry and metadata
3. **Background Tasks**: Periodically fetches server metadata
4. **MCP Server**: Separate process exposing tools to LLM agents

## Contributing

Contributions are welcome! Please ensure:
- Code follows Python style guidelines
- Tests pass
- Documentation is updated

## License

This project is part of the AberOWL framework. See the main repository for license information.
