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

For more detail see `central_server/README.md` (central stack, MCP, and local
testing), `deploy/SELF_HOSTING.md` (single-host design), and `deploy/README.md`
(production deployment).

## Dependencies

- Linux
- Docker and Docker Compose
- Groovy and Anaconda/Miniconda (for local development)

## Quick start

This section brings the central stack and a worker up separately, which is what you
want when you develop against them. To run a working instance over your own
ontologies in one command, go to "Run your own instance" below instead.

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

## Run your own instance

`deploy/docker-compose.selfhost.yml` starts a complete AberOWL 2 on one host, over
ontologies you supply. It brings up six services on an internal Docker network:
Redis, Elasticsearch, the central server, one worker, and two one-shot init
containers. `ontology-prepare` runs first and writes the ontology config the worker
loads; `ontology-register` runs last and registers each classified ontology with the
central server, then builds its search index. The services address each other by
container name, so none of the cross-host IP wiring the production cluster needs
applies here. `deploy/SELF_HOSTING.md` documents the design; this section is the
operating procedure.

### Prerequisites

- Linux, with Docker and the Compose v2 plugin invoked as `docker compose`. We
  verified this procedure on Docker 29.7.2 with Compose v5.5.0.
- Internet access on the first run, to pull the two images. After that the stack
  runs offline, unless you ask it to download ontologies by URL.
- Host ports 8000 and 8766 free, or `CENTRAL_PORT` and `MCP_PORT` set to ports that
  are. When a port is taken, the central server stays in state `Created` and Docker
  reports `failed to bind host port 0.0.0.0:8000/tcp: address already in use`.
- Around 4 GB of free memory for the stack itself (Elasticsearch runs with a 1 GB
  heap), plus whatever the reasoner needs for your ontologies. The worker service
  sets no JVM heap size, so a large corpus can need `JAVA_OPTS=-Xmx<N>g` and a
  `mem_limit` added to it.
- Roughly 1.5 GB of disk for the images, plus space for the Elasticsearch index.

### Images

The compose file names the published images, so `up` needs no local build:

| image | contents |
| --- | --- |
| `kaustborg/aberowl-central:2.0` | FastAPI central server, web UI, MCP server |
| `kaustborg/aberowl-worker:2.0` | Groovy/OWLAPI worker with the ELK, structural and HermiT reasoners |

Each of those two services also keeps a `build:` stanza pointing at its Dockerfile
(`central_server/Dockerfile` and `Dockerfile.api`), which are the source of truth,
so `docker compose -f deploy/docker-compose.selfhost.yml build` rebuilds both from
source under the same names during development.

### Start it

```bash
# The bundled example (the pizza ontology), so this runs with no arguments:
docker compose -f deploy/docker-compose.selfhost.yml up

# Your own ontologies:
ONTOLOGIES_DIR=/absolute/path/to/my-ontologies \
  docker compose -f deploy/docker-compose.selfhost.yml up
```

You supply the value of `ONTOLOGIES_DIR`, and it must be an absolute path. Compose
resolves a relative host path against `deploy/` rather than against your shell's
working directory, so `./my-ontologies` looks inside `deploy/` and finds nothing.

Once the stack settles, two endpoints are published on the host:

| endpoint | default | variable |
| --- | --- | --- |
| web interface and HTTP API | `http://localhost:8000` | `CENTRAL_PORT` |
| MCP endpoint for agents | `http://localhost:8766/mcp` | `MCP_PORT` |

Nothing else is published; Redis, Elasticsearch and the worker stay on the internal
network. Every variable has a working default, so `up` requires none of them.
Change `ADMIN_PASSWORD` (default `changeme`) and `ABEROWL_SECRET_KEY` (default
`selfhost-dev-key`) on any host that others can reach. `deploy/SELF_HOSTING.md`
lists the full set.

Stop the stack, and optionally drop the Elasticsearch index with it:

```bash
docker compose -f deploy/docker-compose.selfhost.yml down
docker compose -f deploy/docker-compose.selfhost.yml down -v
```

### Supplying your own ontologies

`ONTOLOGIES_DIR` points at one folder, and `deploy/selfhost_init.py prepare` reads
it and writes the canonical `ontologies.json` the worker loads. Three input shapes
are supported:

1. **Ontology files.** Drop `.owl` files (also `.owl.gz`, `.rdf`, `.ttl`, `.obo`)
   into the folder. Each id comes from the filename, so `go.owl` becomes `go`, and
   the reasoner defaults to ELK.
2. **A `sources.txt` of URLs**, downloaded when the stack starts. One
   `[id] URL [reasoner]` entry per line; the id and the reasoner are optional, and
   `#` starts a comment. `examples/selfhost/ontologies/sources.txt.example` is a
   template.
3. **An `ontologies.config.json`** for per-ontology control, holding a list of
   `{"id", "path" | "url", "reasoner"}` entries. When this file is present it is
   authoritative and replaces the scan of files and `sources.txt`.

Shapes 1 and 2 combine in one folder, so a folder holding `pizza.owl` and a
`sources.txt` line for BFO loads both. Shape 3 replaces both. The reasoner of an
ontology is `elk` (the default), `structural`, or `hermit`.

The `prepare` step writes into the folder you supply: it creates one subdirectory
per ontology id holding that ontology's file, and writes `ontologies.json`
alongside. Give it a writable folder, and keep it separate from a directory of
ontologies you maintain elsewhere.

Those three shapes are the self-hosting interface. Underneath them, the worker takes
one `ONTOLOGY_PATH` and reads it in one of three ways
(`aberowlapi/OntologyServer.groovy`): a path to a single `.owl` file loads that
ontology alone and derives its id from the filename; a path to a directory loads
every `.owl` file in it; and a path ending in `.json` loads the explicit list of
`{"id", "path", "reasoner"}` entries it holds. The self-hosting stack always uses
the third, because `prepare` writes that list for it.

### What you should see

`ontology-prepare` reports the ontologies it resolved:

```
prepare: wrote /data/ontologies.json with 1 ontology: pizza
```

The worker then loads and classifies each ontology, which is the slow step and
takes minutes for a large one. `ontology-register` waits for that, registers each
ontology, and waits for its search index to populate:

```
register: worker loaded 1 ontology: pizza
  pizza: registered + indexed (100 classes searchable)
register: 1/1 ontologies ready
```

Three HTTP checks confirm the instance answers, on the bundled pizza example:

```bash
curl -s http://localhost:8000/api/listOntologies
# [{"id": "pizza", "title": "pizza", "status": "online"}]

curl -s -G http://localhost:8000/api/dlquery_all \
  --data-urlencode 'query=Pizza' --data-urlencode 'type=subclass' \
  --data-urlencode 'ontology=pizza' --data-urlencode 'direct=true'
# 8 direct subclasses: Cheesy Pizza, Meaty Pizza, Non Vegetarian Pizza,
# Pizza Com Um Nome, Pizza Temperada, Pizza Temperada Equivalente,
# Real Italian Pizza, Vegetarian Pizza

curl -s -G http://localhost:8000/api/search_all --data-urlencode 'query=mozzarella'
# one hit, the MozzarellaTopping class
```

An MCP client that connects to `http://localhost:8766/mcp` over streamable HTTP
lists ten tools: `browse_hierarchy`, `find_iri`, `get_class_info`,
`get_ontology_info`, `list_ontologies`, `list_sparql_examples`, `query_sparql`,
`rewrite_sparql`, `run_dl_query` and `search_classes`. We checked this with an
`initialize` call followed by `tools/list`.

### Registration returns 403

`ontology-register` reports `register <id>: FAILED HTTP 403` when the central
server already holds a registry entry for that ontology id from an earlier run.
Registration is guarded so that an existing entry cannot be repointed at a
different worker. Clear the entry and repeat just that step:

```bash
docker exec aberowl-selfhost-redis redis-cli hdel registered_servers pizza
docker compose -f deploy/docker-compose.selfhost.yml up ontology-register
```

Pass the same `CENTRAL_PORT` and `MCP_PORT` values you started the stack with, or
Compose recreates the central server on its defaults.

If you pulled the images before 2026-09-10, run `docker compose -f
deploy/docker-compose.selfhost.yml pull` first: earlier builds carried a registry
file that produced this on the bundled example.

### The other compose files

`deploy/docker-compose.central.yml` and `deploy/docker-compose.worker.yml` describe
the multi-host production deployment, one file per role, and they are not
interchangeable with the self-hosting file. Both build their images from the
Dockerfiles in this repository and name no published image. The worker file joins an
`aberowl-net` network declared external, which you create beforehand, and it
publishes the worker's own port on the host. The central file requires
`ADMIN_PASSWORD` and `ABEROWL_SECRET_KEY` with no defaults, mounts a host ontology
directory, and binds the MCP port to loopback rather than to all interfaces. The
two roles run on separate hosts and are wired by IP. `deploy/README.md` and
`deploy/PROD_ROLLOUT.md` give that procedure, and `deploy/plan_workers.py`,
`deploy/launch_workers.py` and `deploy/register_workers.py` are the tooling it
uses. To run an instance of your own, use `deploy/docker-compose.selfhost.yml`.

## Reproducing the paper's evaluations

Both evaluations reported in the AberOWL 2.0 paper live under `experiments/`, and
each ships its gold set, its harness, the raw per-run model outputs, and its
scorer. The scorers read only committed files, so **both scoring runs work
offline**: they need no network, no API key, and no running AberOWL instance. Both
use the Python standard library only, and we ran them under Python 3.10.14.

**A reader cannot regenerate the model responses.** Every response came from a paid
API call through OpenRouter, which serves one model identifier from endpoints that
differ in price and in quantization, so the same request need not reach the same
system twice. The raw outputs are therefore committed instead of the means to
recreate them. `harness.py` and `run_model.py` are included so the procedure can be
inspected, and running either costs money and produces different responses.

Each experiment directory carries a `README.md` that documents the design in full.
Its `RESULTS.md` records the working analysis as the runs came in: the grounding one
predates the de-duplicated gold set, and the reasoning one predates the last three
models. The scorer output below, rather than `RESULTS.md`, is what matches the
paper.

### Identifier grounding

`experiments/iri_hallucination/` measures whether the `find_iri` MCP tool reduces
how often an LLM agent hallucinates an ontology-class IRI. Five models resolved
each of 162 terms to a canonical IRI, under two conditions (no tool, and `find_iri`
available but never mentioned in the prompt, so calling it is the model's own
decision) and two regimes (forced to answer, and offered an abstention). Of the 162
terms, 122 name a real class and 40 name none. Scoring covers 3,231 runs.

| file | role |
| --- | --- |
| `gold_dedup.jsonl` | the gold set the paper reports: 162 terms, each with its gold IRI and difficulty level |
| `gold.jsonl`, `dedup_gold.py` | the earlier 173-row set and the script that derives the de-duplicated one from it |
| `harness.py`, `prompts.py`, `config.py` | the harness that runs each model over MCP; needs an OpenRouter key, so it is not reproducible offline |
| `runs_full.jsonl` | raw per-run model outputs, 3,450 runs before the gold filter |
| `iri_exists_resolved.json` | the committed IRI existence map, resolved on 2026-08-19, which makes scoring offline and deterministic |
| `build_exists_map.py` | builds that map against a live AberOWL; it needs network, and re-running it is not required |
| `score.py` | the scorer |
| `score_corrected_by_stratum.txt`, `score_corrected_by_difficulty.txt` | committed scorer output to compare a fresh run against |

This command reproduces **Table 3 of the main text**:

```bash
cd experiments/iri_hallucination
python3 score.py --runs runs_full.jsonl --gold gold_dedup.jsonl --by-stratum
```

The `forced` rows ending in `real` give the left half of Table 3, the accuracy and
hallucination columns over the 122 terms that name a real class. The `abstain` rows
ending in `null-gold` give the right half, where the `abst%` column is the
percentage of the 40 no-class terms a model declined. Substituting
`--by-difficulty` for `--by-stratum` reproduces supplementary Table S2, accuracy by
difficulty level. Both outputs are byte-identical to the committed
`score_corrected_by_stratum.txt` and `score_corrected_by_difficulty.txt`.

### DL query answering

`experiments/dl_reasoning/` measures whether an agent can use the reasoner through
`run_dl_query`. Five models returned the complete *set* of classes satisfying each
of 120 class expressions stated in natural language, under four conditions that
differ only in which tools the API exposes: no tool, the lookup tools alone
(`find_iri` and `search_classes`), those plus `run_dl_query`, and the same three
tools with a worked Manchester syntax example added to the system prompt. Scoring
covers 2,400 runs. The expressions come from the deployed releases of the Gene
Ontology (51,937 classes, 2026-03-25), the Cell Ontology (19,151 classes,
2026-03-26) and the Sequence Ontology (2,752 classes).

| file | role |
| --- | --- |
| `build_dl_gold.groovy` | builds the gold sets by running ELK on the OWL release outside AberOWL, so the service never grades itself, and dumps each release's class universe |
| `gold_all.jsonl` | the gold set: 120 expressions, 60 of each task type. `gold_go.jsonl`, `gold_cl.jsonl` and `gold_so.jsonl` are the per-ontology parts |
| `classes_go.txt`, `classes_cl.txt`, `classes_so.txt` | the class universe of each release, which turns identifier fabrication into an offline set-membership test |
| `run_model.py`, `prompts.py`, `config.py` | the harness, which refuses more than one model per invocation; needs an OpenRouter key, so it is not reproducible offline |
| `runs_gpt-oss-20b.jsonl`, `runs_llama-4-scout.jsonl`, `runs_qwen3.6-35b-a3b.jsonl`, `runs_deepseek-v3.2.jsonl`, `runs_gemini-3.5-flash.jsonl` | raw per-run model outputs, 480 runs per model |
| `score_dl.py` | the scorer |
| `scored_all5.jsonl` | committed per-run scored output to compare a fresh run against |

This command reproduces **Table 4 of the main text**:

```bash
cd experiments/dl_reasoning
python3 score_dl.py --gold gold_all.jsonl \
  --classes classes_go.txt classes_cl.txt classes_so.txt \
  --runs runs_gpt-oss-20b.jsonl runs_llama-4-scout.jsonl \
         runs_qwen3.6-35b-a3b.jsonl runs_deepseek-v3.2.jsonl \
         runs_gemini-3.5-flash.jsonl
```

The `exact` column holds Table 4, one row per model, condition and task type, with
`n` at 60 in every cell. The columns to its right decompose each reasoning-arm
result into adoption, formulation and relay, which supplementary Section S5
reports. Adding `--out scored_all5.jsonl` regenerates the committed per-run scored
file byte-identically.

`--runs` also accepts a glob, and the five file names are worth spelling out
anyway. A working tree that still holds a partial run file from an interrupted
harness invocation (`runs_*.laptop-partial.jsonl`, which git ignores) matches
`runs_*.jsonl` as well, and mixing one in pushes cells past 60 items and shifts the
reported numbers.
