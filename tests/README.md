# AberOWL2 Integration Tests

## Quick start

```bash
# From the repository root
uv run --extra test pytest tests/ -v
```

---

## What is tested

| Test | File | Marker | What it verifies |
|------|------|--------|-----------------|
| `test_go_manchester_subclass_query` | `test_integration.py` | `slow` | GO loaded + classified by ELK; Manchester subClassOf query returns known children of biological\_process (GO:0008150) |
| `test_bioportal_fetch_dedup` | `test_integration.py` | `bioportal` | BioPortal REST API returns ontologies; OBOFoundry IDs are excluded; schema is correct |
| `test_es_search_via_groovy_api` | `test_integration.py` | `slow` | pizza stack proxies an ES query via `elastic.groovy`; the Pizza class is found |
| `test_central_virtuoso_sparql` | `test_integration.py` | `slow` | `CentralVirtuosoManager` inserts triples into a named graph; triple count matches via HTTP SPARQL |
| `test_ontology_update_hotswap` | `test_integration.py` | `slow` | `updateOntology.groovy` hot-swaps pizza; `updateStatus.groovy` reports success; subsequent query still works |

---

## Selective execution

```bash
# Only fast tests (no Docker)
uv run --extra test pytest tests/ -v -m "not slow and not bioportal"

# Only the BioPortal fetch test
uv run --extra test pytest tests/ -v -m bioportal

# Skip BioPortal but run everything else
uv run --extra test pytest tests/ -v -m "not bioportal"

# A single test by name
uv run --extra test pytest tests/ -v -k test_ontology_update_hotswap
```

---

## Prerequisites

### Software

| Tool | Purpose |
|------|---------|
| Docker (≥ 24) with Compose v2 | Container management |
| `uv` | Python package / test runner |
| `groovy` in PATH (inside containers) | Groovlet runtime |

### Files that must exist

| Path | Description |
|------|-------------|
| `data/go.owl` | Gene Ontology (122 MB); sourced from `http://purl.obolibrary.org/obo/go.owl` |
| `data/pizza.owl` | Pizza ontology (160 KB); included in repo |
| `Dockerfile.api` | Per-ontology API image |
| `Dockerfile.nginx` | Nginx image |
| `Dockerfile.virtuoso` | Virtuoso image (used by central\_virtuoso fixture) |
| `docker-compose.yml` | New-format per-ontology compose file |

---

## Fixtures (`conftest.py`)

All fixtures are **session-scoped** — Docker containers start once and are shared across the whole test session.

### `central_es`

Starts `elasticsearch:7.17.10` as a standalone container, exposes port `PORT_ES` (default **19200**) on the host, and waits up to 120 s for the cluster to become healthy.

Yields: `http://localhost:19200`

Teardown: `docker rm -f aberowl_test_es`

### `central_virtuoso`

Builds the Virtuoso image from `Dockerfile.virtuoso`, starts it, exposes SPARQL HTTP on `PORT_VIRT` (default **18890**), waits up to 90 s.

Yields: `http://localhost:18890`

Teardown: `docker rm -f aberowl_test_virtuoso`

### `pizza_stack`

Depends on `central_es` and `central_virtuoso`.

1. Creates `{ONT_HOST_PATH}/pizza/pizza_active.owl` (copied from `data/pizza.owl`).
2. Writes a new-format env file with `ONTOLOGY_ID=pizza`, `NGINX_PORT=PORT_PIZZA` (default **8082**), and sets `ELASTICSEARCH_URL` / `CENTRAL_VIRTUOSO_URL` pointing to the test ES and Virtuoso.
3. Runs `docker compose up --build -d`.
4. Polls `/api/health.groovy` for up to 180 s.

Yields: `http://localhost:8082/api`

Teardown: `docker compose down -v`

### `go_stack`

Same as `pizza_stack` but with `data/go.owl` and port `PORT_GO` (default **8080**). Poll timeout is **600 s** (10 minutes) because ELK classification of the 47k-class GO takes several minutes.

Yields: `http://localhost:8080/api`

---

## Port configuration

All ports can be overridden with environment variables so the tests can run alongside other services:

| Env var | Default | Purpose |
|---------|---------|---------|
| `ABEROWL_TEST_PORT_PIZZA` | `8082` | nginx port for pizza stack |
| `ABEROWL_TEST_PORT_GO` | `8080` | nginx port for GO stack |
| `ABEROWL_TEST_CENTRAL_PORT` | `8099` | central-server port |
| `ABEROWL_TEST_ES_PORT` | `19200` | host-side ES port |
| `ABEROWL_TEST_VIRTUOSO_PORT` | `18890` | host-side Virtuoso HTTP port |
| `ABEROWL_TEST_ONT_PATH` | `/tmp/aberowl_test_ontologies` | host path for shared OWL files |
| `ABEROWL_REPO_PATH` | (parent of `tests/`) | repository root |

Example — run on non-default ports to avoid conflicts:

```bash
ABEROWL_TEST_ES_PORT=29200 ABEROWL_TEST_VIRTUOSO_PORT=28890 \
    uv run --extra test pytest tests/ -v -m slow
```

---

## Test details

### 1 · `test_go_manchester_subclass_query`

**Marker**: `slow`
**Timeout**: 900 s
**Fixture**: `go_stack`

Sends a GET to `/api/runQuery.groovy`:

```
query  = <http://purl.obolibrary.org/obo/GO_0008150>
type   = subclass
direct = true
labels = true
axioms = false
```

Assertions:
- Response contains `result` list with at least one entry.
- Every entry has `class` (IRI string) and `label` fields.
- IRI `http://purl.obolibrary.org/obo/GO_0000003` (reproduction) is in the result set — a well-known direct child of biological\_process.

### 2 · `test_bioportal_fetch_dedup`

**Marker**: `bioportal`
**Timeout**: 300 s
**Fixture**: none

Calls the internal `_get_json` helper from `central_server/app/intake/bioportal.py` directly to fetch page 1 of the BioPortal ontology list (100 entries). This avoids the expensive per-ontology download-URL round-trips that the full `fetch_bioportal_ontologies()` call performs.

Assertions:
- Page 1 returns at least one entry.
- After filtering `exclude_ids = {"hp", "go", "aro", "chebi", "bfo", "ro"}`, at least one candidate remains.
- None of the excluded IDs appears in the filtered candidate list.
- Each candidate has `acronym` and `name` fields.

### 3 · `test_es_search_via_groovy_api`

**Marker**: `slow`
**Timeout**: 300 s
**Fixture**: `pizza_stack` (which also starts `central_es`)

Steps:
1. Creates index `aberowl_pizza_classes_v1` in the test ES directly via HTTP.
2. Inserts a document `{label: "Pizza", class: "…#Pizza", ontology: "pizza"}` with `?refresh=true`.
3. Queries via the pizza ontology-api's `elastic.groovy` proxy:
   ```
   GET /api/elastic.groovy?index=aberowl_pizza_classes_v1&source={"query":{"term":{"label":"Pizza"}}}
   ```
4. Asserts at least one hit is returned with the correct `label` and `ontology`.

This validates that `ELASTICSEARCH_URL` is wired correctly inside the container and that the Groovy HTTP proxy works end-to-end.

### 4 · `test_central_virtuoso_sparql`

**Marker**: `slow`
**Timeout**: 120 s
**Fixture**: `central_virtuoso`

Uses `CentralVirtuosoManager` (sets `VIRTUOSO_URL` and `VIRTUOSO_DBA_PASSWORD` env vars before instantiation):

1. Inserts 8 triples into graph `http://aberowl.net/ontology/test_pizza` via `_execute_update()`.
2. Calls `get_triple_count("test_pizza")` and asserts result ≥ 8.
3. Queries the raw SPARQL HTTP endpoint (`/sparql`) to get a COUNT(*) independently.
4. Asserts both counts agree.
5. Drops the test graph.

### 5 · `test_ontology_update_hotswap`

**Marker**: `slow`
**Timeout**: 300 s
**Fixture**: `pizza_stack`

Full hot-swap lifecycle:

1. Copies `pizza_active.owl` → `pizza_staging.owl` in the shared volume (simulating a newly downloaded version).
2. POSTs to `/api/updateOntology.groovy`:
   ```json
   {"owlPath": "/data/pizza_staging.owl", "secretKey": "<TEST_SECRET_KEY>"}
   ```
3. Asserts response `{"status": "accepted", "taskId": "…"}`.
4. Polls `/api/updateStatus.groovy?taskId=…` every 5 s until status ≠ `pending`.
5. Asserts final status is `success`.
6. Issues a `runQuery.groovy` subClassOf query for the Pizza class and asserts results are non-empty — confirming the new `RequestManager` is live and the old one was disposed.

---

## Common failure modes

| Symptom | Likely cause |
|---------|-------------|
| `go_stack` fixture times out | GO classification took > 10 min; increase `ABEROWL_TEST_TIMEOUT_GO` or check container RAM |
| `central_virtuoso` fixture fails | `Dockerfile.virtuoso` build error or `DBA_PASSWORD` not accepted; check Virtuoso container logs |
| `test_bioportal_fetch_dedup` skipped or fails | BioPortal API key expired or rate limited; update `BIOPORTAL_API_KEY` in `bioportal.py` |
| `test_es_search_via_groovy_api` — 0 hits | ES document not refreshed in time; the test uses `?refresh=true` which forces immediate visibility |
| `test_ontology_update_hotswap` — `status=failed` | `ABEROWL_SECRET_KEY` mismatch or OWL file path wrong; check container env and volume mount |
