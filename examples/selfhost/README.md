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

That folder can contain, in increasing order of control:

1. **Bare files** — drop `myont.owl` in; its id becomes `myont`, reasoner ELK.
2. **Web sources** — add a `sources.txt` (see `ontologies/sources.txt.example`)
   listing URLs to download on startup.
3. **Full control** — add an `ontologies.config.json`:
   ```json
   [
     {"id": "myont", "path": "myont.owl", "reasoner": "elk"},
     {"id": "go",    "url": "http://purl.obolibrary.org/obo/go.owl"}
   ]
   ```

See [`deploy/SELF_HOSTING.md`](../../deploy/SELF_HOSTING.md) for details.
