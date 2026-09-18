# Self-host example

A ready-to-run folder for the single-host AberOWL 2 setup. It ships one small
ontology (`ontologies/pizza.owl`) so the stack comes up with something to query.

## Run it

From the repository root:

```bash
docker compose -f deploy/docker-compose.selfhost.yml up
```

When it settles:

- Web / API: <http://localhost:8000>
- AI-agent endpoint (MCP): <http://localhost:8766/mcp>

## Use your own ontologies

Point `ONTOLOGIES_DIR` at your own folder, with an **absolute path**:

```bash
ONTOLOGIES_DIR=$PWD/my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
```

Use `$PWD`, not a bare relative path like `./my-ontologies`: Compose resolves a
relative host path against the compose file's own directory (`deploy/`), not
your current directory, so a bare `./my-ontologies` looks for `deploy/my-ontologies`
and finds nothing; see the default (`../examples/selfhost/ontologies`) and the
header comment in `deploy/docker-compose.selfhost.yml` for the same rule.

The folder accepts both local files and URL lists:

- **Files**: drop `.owl` files in directly (id from the filename, reasoner ELK).
- **URLs**: add a `sources.txt` (see `ontologies/sources.txt.example`) listing
  URLs to download on startup.

Both are loaded. For example, this folder loads **both** pizza (file) and bfo (URL):

```
my-ontologies/
  pizza.owl          # a local file
  sources.txt        # one line:  bfo  http://purl.obolibrary.org/obo/bfo.owl
```

For per-ontology control, add an
`ontologies.config.json` (authoritative; it replaces the files/`sources.txt` scan):
```json
[
  {"id": "myont", "path": "myont.owl", "reasoner": "elk"},
  {"id": "go",    "url": "http://purl.obolibrary.org/obo/go.owl"}
]
```

See [`deploy/SELF_HOSTING.md`](../../deploy/SELF_HOSTING.md) for details.

## Requirements

See `deploy/docker-compose.selfhost.yml` for the exact settings.

- **Memory.** Elasticsearch uses a 1 GB heap (`ES_JAVA_OPTS=-Xms1g -Xmx1g`).
  The Compose file does not set `mem_limit`, `deploy.resources`, or `JAVA_OPTS`
  for the `worker`, `central-server`, or `redis` services. It therefore does not
  fix their memory use or impose a memory floor or ceiling beyond the ES heap.
  The bundled small `pizza.owl` example works with these defaults. Larger
  ontology sets require more worker heap to classify and retain their classes
  and axioms. Size the host for the corpus and configure memory limits, for
  example through `JAVA_OPTS` and `mem_limit` overrides.
- **Offline vs. network.** The container images (`kaustborg/aberowl-worker:2.0`,
  `kaustborg/aberowl-central:2.0`, `redis:alpine`, `elasticsearch:7.17.10`) need
  network access to pull the first time; after that, `up` runs offline. Ontology
  files placed directly in `ONTOLOGIES_DIR` are read locally and need no network.
  A `sources.txt` (or an `ontologies.config.json` entry with a `url`) is the
  exception: the one-shot `ontology-prepare` service downloads those URLs on every
  `up`, so that path needs network each time, not just once.
