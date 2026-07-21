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

Point `ONTOLOGIES_DIR` at your own folder:

```bash
ONTOLOGIES_DIR=./my-ontologies docker compose -f deploy/docker-compose.selfhost.yml up
```

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
