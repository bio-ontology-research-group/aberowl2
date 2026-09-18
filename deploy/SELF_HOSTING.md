# Self-hosting AberOWL 2

Run a single-host instance over your own ontologies with Docker Compose. The
worker classifies ontologies locally and exposes them through the central API
and MCP server. Downloads require network access. If you connect an external
LLM provider, the agent may send ontology terms and tool results to that provider;
configure the agent to meet your data-handling requirements.

## How you feed in ontologies

Point `ONTOLOGIES_DIR` at one folder. The folder accepts both local files and URL lists:

- **Files**: drop `.owl` files in directly. Each file's id comes from its name
  (`myont.owl` → `myont`), reasoner defaults to ELK.
- **URLs**: add a `sources.txt` listing ontologies to download on startup, one
  `[id] URL [reasoner]` per line (e.g. OBO Foundry PURLs; `#` comments allowed).

Both are read. For example, a folder holding `pizza.owl` **and** a `sources.txt` with
`bfo http://purl.obolibrary.org/obo/bfo.owl` loads both: pizza from the file and
bfo from the URL:

```
my-ontologies/
  pizza.owl          # a local file
  sources.txt        # lists URLs to fetch (one is: bfo  http://purl.obolibrary.org/obo/bfo.owl)
```

For per-ontology control, add an `ontologies.config.json` with a list of
`{"id", "path" | "url", "reasoner"}` objects. When present it is
**authoritative and replaces** the files/`sources.txt` scan.

The `ontology-prepare` step turns whatever it finds into a single canonical
`ontologies.json` that the worker loads (`deploy/selfhost_init.py`, unit-tested in
`tests/test_selfhost_init.py`).

## Before you start

- Linux, with Docker and the Compose v2 plugin invoked as `docker compose`.
  Verified on Docker 29.7.2 with Compose v5.5.0.
- Internet access on the first run, to pull the two images. After that the stack
  runs offline, unless you ask it to download ontologies by URL.
- Host ports 8000 and 8766 free, or `CENTRAL_PORT` and `MCP_PORT` set to ports
  that are.
- Around 4 GB of free memory for the stack itself, since Elasticsearch runs with
  a 1 GB heap, plus whatever the reasoner needs for your ontologies.
- Roughly 1.5 GB of disk for the images, plus space for the Elasticsearch index.

## Start the service

```bash
# defaults to examples/selfhost/ontologies (the pizza ontology) so `up` just works:
docker compose -f deploy/docker-compose.selfhost.yml up
# your own set:
ONTOLOGIES_DIR=$PWD/my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
#   web / API -> http://localhost:8000
#   MCP       -> http://localhost:8766/mcp   (agent endpoint)
```

## What you should see

`ontology-prepare` runs first and reports what it resolved:

```
prepare: wrote /data/ontologies.json with 1 ontology: pizza
```

The worker then loads and classifies each ontology. This is the slow step and
takes minutes for a large one. `ontology-register` waits for it, registers each
ontology and waits for its search index to fill:

```
register: worker loaded 1 ontology: pizza
  pizza: registered + indexed (100 classes searchable)
register: 1/1 ontologies ready
```

Three requests confirm the instance answers, using the bundled pizza example:

```bash
curl -s http://localhost:8000/api/listOntologies
# [{"id": "pizza", "title": "pizza", "status": "online"}]

curl -s -G http://localhost:8000/api/dlquery_all \
  --data-urlencode 'query=Pizza' --data-urlencode 'type=subclass' \
  --data-urlencode 'ontology=pizza' --data-urlencode 'direct=true'
# the direct subclasses of Pizza, including Cheesy Pizza and Vegetarian Pizza

curl -s -G http://localhost:8000/api/search_all --data-urlencode 'query=mozzarella'
# one hit, the MozzarellaTopping class
```

An MCP client connecting to `http://localhost:8766/mcp` over streamable HTTP
lists ten tools: `browse_hierarchy`, `find_iri`, `get_class_info`,
`get_ontology_info`, `list_ontologies`, `list_sparql_examples`, `query_sparql`,
`rewrite_sparql`, `run_dl_query` and `search_classes`.

## When something goes wrong

**A host port is already taken.** The central server stays in state `Created`
and Docker reports `failed to bind host port 0.0.0.0:8000/tcp: address already
in use`. Set `CENTRAL_PORT` or `MCP_PORT` to a free port and start again.

**Registration returns 403.** `ontology-register` reports
`register <id>: FAILED HTTP 403` when the central server already holds a
registry entry for that ontology id from an earlier run. Registration is guarded
so an existing entry cannot be repointed at a different worker. Clear the entry
and repeat that one step:

```bash
docker exec aberowl-selfhost-redis redis-cli hdel registered_servers pizza
docker compose -f deploy/docker-compose.selfhost.yml up ontology-register
```

**The worker is killed while classifying.** The worker sets no JVM heap limit,
so it takes the container default. Give it a `mem_limit` and set `JAVA_OPTS`
with an explicit `-Xmx` for a large ontology set.

## How it fits together

Six services share a Compose network named `aberowl-net`:
`redis`, `elasticsearch`, `central-server`, one `worker`, and two one-shot services:
`ontology-prepare` (download + write `ontologies.json`, before the worker) and
`ontology-register` (register each loaded ontology with central + trigger its index,
after the worker classifies).

## Settings

Every variable has a working default, so `up` does not require overrides. Override by
exporting the variable or supplying a private environment file with Compose
`--env-file`.

| Variable | Default | What it does |
|---|---|---|
| `ONTOLOGIES_DIR` | the bundled `examples/selfhost/ontologies` | Absolute path to your ontology folder. Relative paths resolve against `deploy/`, not your shell's directory. |
| `CENTRAL_PORT` | `8000` | Host port for the central server and web UI. |
| `MCP_PORT` | `8766` | Host port for the MCP endpoint. |
| `ADMIN_USER` | `admin` | Admin user for the management endpoints. |
| `ADMIN_PASSWORD` | `changeme` | Admin password. Change it on any host others can reach. |
| `ABEROWL_SECRET_KEY` | `selfhost-dev-key` | Shared secret the central server uses to call the worker's mutating endpoints. Change it alongside the password. |

The worker does not set a JVM heap limit, so it takes the container default. A large
ontology set may need one; give the worker a `mem_limit` and set `JAVA_OPTS`
if you hit an out-of-memory kill.

## The other compose files

`deploy/docker-compose.central.yml` and `deploy/docker-compose.worker.yml`
describe the multi-host deployment, one file per role, and are not
interchangeable with the self-hosting file. Both build their images from the
Dockerfiles in this repository and name no published image. The worker file
joins an `aberowl-net` network declared external, which you create beforehand,
and publishes the worker's own port. The central file requires `ADMIN_PASSWORD`
and `ABEROWL_SECRET_KEY` with no defaults, mounts a host ontology directory, and
binds the MCP port to loopback rather than to all interfaces. The two roles run
on separate hosts. [`README.md`](README.md) gives that procedure, and
`deploy/plan_workers.py`, `deploy/launch_workers.py` and
`deploy/register_workers.py` are its tooling. To run an instance of your own,
use `deploy/docker-compose.selfhost.yml`.

## Notes

- This stack runs one worker. Larger corpora can use the bulk worker provisioning
  workflow with separate per-worker configurations and registration.
- See [Deployment and administration](README.md) for the separate central stack
  and bulk worker provisioning.
