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

You feed ontologies to that folder in two ways that **work together in the same
folder** — you don't pick one:

- **Files** — drop `.owl` files in directly (id from the filename, reasoner ELK).
- **URLs** — add a `sources.txt` (see `ontologies/sources.txt.example`) listing
  URLs to download on startup.

Both are loaded. For example, this folder loads **both** pizza (file) and bfo (URL):

```
my-ontologies/
  pizza.owl          # a local file
  sources.txt        # one line:  bfo  http://purl.obolibrary.org/obo/bfo.owl
```

**Advanced, instead of the above** — for per-ontology control, add an
`ontologies.config.json` (authoritative; it replaces the files/`sources.txt` scan):
```json
[
  {"id": "myont", "path": "myont.owl", "reasoner": "elk"},
  {"id": "go",    "url": "http://purl.obolibrary.org/obo/go.owl"}
]
```

See [`deploy/SELF_HOSTING.md`](../../deploy/SELF_HOSTING.md) for details.

## Requirements

Read `deploy/docker-compose.selfhost.yml` for the exact settings; summarized here:

- **Memory.** The compose file fixes one heap size explicitly: Elasticsearch's,
  via `ES_JAVA_OPTS=-Xms1g -Xmx1g` (1 GB). It sets no `mem_limit`, `deploy.resources`,
  or `JAVA_OPTS` for the `worker` or `central-server` services, and none for `redis`
  either, so their memory footprint is **not fixed by the compose file**; for the
  bundled example (the small `pizza.owl` ontology) that is not a problem in practice,
  but there is no compose-enforced floor or ceiling to quote beyond the 1 GB ES
  heap. A larger corpus in `ONTOLOGIES_DIR` scales the worker's JVM heap with the
  size of the loaded ontologies (more classes/axioms need more heap to classify and
  hold in memory); size the host accordingly, but this file does not fix that number
  either; it must be set by the operator (e.g. a `JAVA_OPTS`/`mem_limit` override)
  for anything beyond the bundled example.
- **Offline vs. network.** The container images (`kaustborg/aberowl-worker:2.0`,
  `kaustborg/aberowl-central:2.0`, `redis:alpine`, `elasticsearch:7.17.10`) need
  network access to pull the first time; after that, `up` runs offline. Ontology
  files placed directly in `ONTOLOGIES_DIR` are read locally and need no network.
  A `sources.txt` (or an `ontologies.config.json` entry with a `url`) is the
  exception: the one-shot `ontology-prepare` service downloads those URLs on every
  `up`, so that path needs network each time, not just once.
