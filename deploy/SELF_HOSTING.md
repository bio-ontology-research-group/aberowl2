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

## Start the service

```bash
# defaults to examples/selfhost/ontologies (the pizza ontology) so `up` just works:
docker compose -f deploy/docker-compose.selfhost.yml up
# your own set:
ONTOLOGIES_DIR=$PWD/my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
#   web / API -> http://localhost:8000
#   MCP       -> http://localhost:8766/mcp   (agent endpoint)
```

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

## Notes

- This stack runs one worker. Larger corpora can use the bulk worker provisioning
  workflow with separate per-worker configurations and registration.
- See [Deployment and administration](README.md) for the separate central stack
  and bulk worker provisioning.
