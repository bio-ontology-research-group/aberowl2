# Aber-OWL 2: Distributed ontology query system

Aber-OWL 2 queries and reasons over biological and biomedical ontologies.
It reorganizes the original Aber-OWL into a **central server** and **worker
containers** that host, classify, and query hundreds of ontologies in parallel.
Each worker holds one or more ontologies in memory and answers Description Logic
queries through a reasoner.

## What it does

- **DL (Description Logic) queries**: query class hierarchies in Manchester OWL
  Syntax (`subclass`, `superclass`, `equivalent`, `subeq`, `supeq`), backed by the
  ELK reasoner. Accepts an IRI or a label, including class expressions such as
  `'part of' some 'cell'`.
- **Full-text search**: find classes and ontologies by label, synonym, or OBO ID
  across the whole corpus, via Elasticsearch (boosted `dis_max`).
- **Ontology browsing (web UI)**: a React single-page app to browse the class
  hierarchy and class metadata, with deep-linkable URLs per ontology / class / query.
- **SPARQL rewriting**: `/api/sparql` rewrites SPARQL that embeds OWL DL frames
  (e.g. `VALUES ?x { OWL subeq go-plus { 'cell death' } }`) into plain SPARQL with the
  concrete IRIs spliced in. The caller can execute it at a SPARQL endpoint, or
  use the MCP `query_sparql` tool to submit it to an external endpoint.
  Aber-OWL does not host a triple store.
- **MCP server**: exposes search / reasoning / SPARQL-rewrite to LLM agents (Claude
  Desktop, Claude Code, etc.) over the Model Context Protocol.
- **Automatic intake**: daily sync of ontology metadata from OBO Foundry and
  BioPortal.

## Architecture

**Central server** (`central_server/`, one Docker Compose stack):

- **FastAPI app**: ontology registry, query aggregator/dispatcher, Elasticsearch-backed
  search, SPARQL rewriter, the web frontend (SPA), and source-sync.
- **Elasticsearch**: shared class/ontology full-text index.
- **Redis**: registry and rate-limit state.
- **MCP server**: auto-spawned alongside the app when `ENABLE_MCP=true`.

**Workers** (`docker-compose.yml`, one JVM per container):

- A Groovy/OWLAPI server (Jetty) that loads one or many ontologies, classifies them
  with a reasoner (**ELK** by default; **Structural** and **HermiT** also available),
  and answers DL queries. Each worker registers its ontologies with the central
  server, which then dispatches each query to the correct worker by `ontologyId`.

For more detail see [central_server/README.md](central_server/README.md)
(central stack, MCP and local testing), [deploy/README.md](deploy/README.md)
(deployment), and [deploy/SELF_HOSTING.md](deploy/SELF_HOSTING.md)
(a private instance).

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
ONTOLOGIES_DIR=$PWD/my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
```

The folder accepts local `.owl` files and a `sources.txt` list of URLs to
download at startup, such as OBO Foundry PURLs. The worker loads both sources:

```
my-ontologies/
  pizza.owl          # a local file
  sources.txt        # one line:  bfo  http://purl.obolibrary.org/obo/bfo.owl
```

This configuration loads pizza from the local file and downloads bfo from its URL.
For per-ontology control, add an `ontologies.config.json` file. This file replaces
the scan of local files and `sources.txt`.

The stack starts Elasticsearch, Redis, the central server, and one worker on an
internal network. It then loads, classifies, and indexes each ontology for search.

See [`deploy/SELF_HOSTING.md`](deploy/SELF_HOSTING.md) and the ready-to-run
[`examples/selfhost/`](examples/selfhost/).

## Docker images

The self-hosting stack pulls two prebuilt images from Docker Hub:
[`kaustborg/aberowl-central`](https://hub.docker.com/r/kaustborg/aberowl-central) (FastAPI
API + web UI + MCP server) and
[`kaustborg/aberowl-worker`](https://hub.docker.com/r/kaustborg/aberowl-worker)
(Groovy/OWLAPI + ELK reasoner). The stack uses these images without a local build.
To rebuild them from source:

```bash
docker compose -f deploy/docker-compose.selfhost.yml build
```

Each service keeps a `build:` stanza pointing at its Dockerfile
(`central_server/Dockerfile`, `Dockerfile.api`), which are the source of truth;
production builds from them directly.

## Paper evaluation

Reproduce the reported evaluation from saved responses with Python 3.10 or
later, using only the standard library. From the repository root:

```bash
python3 experiments/reproduce.py --out-dir results/paper-reproduction
```

The output directory must be empty; choose another `--out-dir` for a subsequent
run. This command does not require a running service, model credentials or
network access. It uses explicit input files, checks the manuscript's main tables,
repeat statistics, paired contrasts and reasoning diagnostics, and writes
per-item scores, text tables and `report.json` with input checksums. A mismatch
exits with an error. It does not modify the saved inputs.

| Evaluation | Saved inputs and scope | Reported outputs |
| --- | --- | --- |
| [Grounding](experiments/iri_hallucination/README.md) | 162 scoped terms (122 positive, 40 constructed negative), five models and two tool conditions under two response regimes | 3,231 scored runs; accuracy, identifier existence and declines; repeated-prompt analysis |
| [Deterministic grounding](experiments/iri_hallucination/direct_lookup/README.md) | Saved exact-resolver responses for the same terms | 117/122 positives return one correct exact match; five are ambiguous; 40/40 negatives have no exact match |
| [DL query answering](experiments/dl_reasoning/README.md) | 120 expressions over three ontologies, five models and four conditions | 2,400 scored runs; exact answer-set match, formulation and relay diagnostics |

Run the offline reproduction validation tests with:

```bash
python3 tests/test_paper_reproduction.py -v
```

New model runs require dependencies, external services and paid API access;
their instructions are separate in the experiment READMEs. The saved responses
support offline rescoring. Missing historical service commits, incomplete
provider records and missing original ontology checksums limit exact repetition
of the model experiments. The deterministic baseline uses cached resolver
responses from August 2026 and reports no-exact-match detection separately
from agent abstention.

The dated deployment observations and service-contract evidence are under
[deploy/measurements/](deploy/measurements/). We maintain the manuscript in
a separate repository. These evaluation commands run independently of it.

The release documents [dependencies](release/DEPENDENCIES.md),
[provenance](release/PROVENANCE.md), [third-party notices](release/THIRD_PARTY_NOTICES.md),
and [credential handling and checksums](release/SECURITY.md).
