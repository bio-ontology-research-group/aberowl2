# Aber-OWL 2: Distributed Ontology Query System

Aber-OWL 2 is a distributed system for querying and reasoning over biological and
biomedical ontologies. It re-architects the original Aber-OWL into a **central
server** plus a fleet of **worker containers**, so that hundreds of ontologies can
be hosted, classified, and queried in parallel — each worker holds one (or many)
ontologies in memory and answers Description Logic queries against a live reasoner.

## What it does

- **DL (Description Logic) queries** — query class hierarchies in Manchester OWL
  Syntax (`subclass`, `superclass`, `equivalent`, `subeq`, `supeq`), backed by the
  ELK reasoner. Accepts an IRI or a label, including class expressions such as
  `'part of' some 'cell'`.
- **Full-text search** — find classes and ontologies by label, synonym, or OBO ID
  across the whole corpus, via Elasticsearch (boosted `dis_max`).
- **Ontology browsing (web UI)** — a React single-page app to browse the class
  hierarchy and class metadata, with deep-linkable URLs per ontology / class / query.
- **SPARQL rewriting** — `/api/sparql` rewrites SPARQL that embeds OWL DL frames
  (e.g. `VALUES ?x { OWL subeq go-plus { 'cell death' } }`) into plain SPARQL with the
  concrete IRIs spliced in; you then run the result against any SPARQL endpoint
  (Ontobee, UniProt, Wikidata, …). Aber-OWL rewrites only — it does not host a triple
  store.
- **MCP server** — exposes search / reasoning / SPARQL-rewrite to LLM agents (Claude
  Desktop, Claude Code, etc.) over the Model Context Protocol.
- **Automatic intake** — daily sync of ontology metadata from OBO Foundry and
  BioPortal.

## Architecture

**Central server** (`central_server/`, one Docker Compose stack):
- **FastAPI app** — ontology registry, query aggregator/dispatcher, Elasticsearch-backed
  search, SPARQL rewriter, the web frontend (SPA), and source-sync.
- **Elasticsearch** — shared class/ontology full-text index.
- **Redis** — registry and rate-limit state.
- **MCP server** — auto-spawned alongside the app when `ENABLE_MCP=true`.

**Workers** (`docker-compose.yml`, one JVM per container):
- A Groovy/OWLAPI server (Jetty) that loads one or many ontologies, classifies them
  with a reasoner (**ELK** by default; **Structural** and **HermiT** also available),
  and answers DL queries. Each worker registers its ontologies with the central
  server, which then dispatches each query to the correct worker by `ontologyId`.

For more detail see `central_server/README.md` (central stack + MCP + local testing),
`deploy/README.md` (production deployment), and `CLAUDE.md` (full architecture map).

## Dependencies

- Linux
- Docker and Docker Compose
- Groovy and Anaconda/Miniconda (for local development)

## Quick start

### 1. Central stack
```bash
cd central_server
docker compose up -d
# central API:  http://localhost:8000
# MCP server:   http://localhost:8766/mcp
```

### 2. A single-ontology worker
Place your ontology in `./data`, then choose a port and start a worker:
```bash
cp /path/to/your_ontology.owl ./data/
./start_docker.sh data/your_ontology.owl 89   # nginx reverse proxy on port 89
```
Shut it down with:
```bash
./shutdown_docker.sh 89
```

For multi-ontology workers and an end-to-end local test (central + worker + a couple
of ontologies), see `central_server/README.md` → "Local end-to-end testing".

## Self-hosting your own instance

Run a private, single-host AberOWL 2 over your own ontologies with one command. Your
ontologies never leave your machine, and an AI agent can reason over them locally
through the built-in MCP server.

```bash
# defaults to a bundled example (the pizza ontology), so this works out of the box:
docker compose -f deploy/docker-compose.selfhost.yml up
#   web / API -> http://localhost:8000
#   MCP       -> http://localhost:8766/mcp

# your own ontologies — point at one folder:
ONTOLOGIES_DIR=./my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
```

You feed ontologies to that one folder in two ways that **work together in the same
folder** — drop `.owl` **files** in directly, and/or add a `sources.txt` listing **URLs**
to download on startup (e.g. OBO Foundry PURLs). Both are loaded:

```
my-ontologies/
  pizza.owl          # a local file
  sources.txt        # one line:  bfo  http://purl.obolibrary.org/obo/bfo.owl
```

The example above loads **both** pizza (from the file) and bfo (downloaded from the URL).
For per-ontology control instead, add an `ontologies.config.json` (authoritative — it
replaces the files/`sources.txt` scan). It brings up Elasticsearch, Redis, the central
server, and one worker on an internal network, then loads, classifies, and indexes each
ontology for search — no cross-host wiring.

See [`deploy/SELF_HOSTING.md`](deploy/SELF_HOSTING.md) and the ready-to-run
[`examples/selfhost/`](examples/selfhost/).

## Developing mode

Prebuilt images are published on DockerHub. To rebuild a worker image from local
source instead, toggle the build/`dockerhub-compose.yml` lines in `start_docker.sh`
(see the comments there).
