# AberOWL2 changelog

## 2026-09-18: Release preparation

### Removed
- The standalone LLM query parser services (`agents/query_parser.py`,
  `agents/query_parser_v2.py`, `Dockerfile.llm`) and the worker interface's LLM
  tab. Natural-language query parsing is no longer part of the worker. The MCP
  server and the evaluation code are unaffected.
- The legacy Manchester-syntax parser classes, the standalone Elasticsearch
  indexer image and its entrypoint, the retired worker SPARQL servlet's tests,
  and superseded derived scoring outputs. The scorers that regenerate those
  outputs are retained.
- Five fixed-port worker diagnostics, superseded by
  `tests/test_servlet_integration.py`, which now asserts concrete IRIs, the
  label-to-superclass contract and the spaced and quoted entity-name cases.

### Fixed
- `shutdown_docker.sh` wrote a Compose override for `elasticsearch` and
  `indexer` services that no longer exist in the worker stack, and removed a
  volume belonging to that retired layout. It now delegates to
  `reload_docker.sh --stop`.
- `start_docker.sh` read the wrong positional argument, so its documented
  `central_es_url` parameter never took effect.
- The README quick-start passed a file path where an ontology id is expected and
  placed the ontology where the worker does not mount it. It now documents the
  real `<ontologies_dir>/<id>/<id>_active.owl` convention.

### Changed
- `central_server/tests/` now runs in CI and is part of `testpaths`. It was
  previously collected by neither.
- The self-hosting privacy wording no longer claims unconditionally that
  ontologies never leave the machine, since an external LLM provider receives
  ontology terms and tool results.
- Site-specific hosts, logins and paths were removed from retained scripts,
  documentation and Compose comments.

## 2026-04-03 / 2026-04-04: Complete system overhaul

### Phase 1: Multi-ontology container architecture
- **`aberowlapi/src/RequestManager.groovy`**: Complete rewrite. Single-ontology fields
  replaced with `ConcurrentHashMap<String, ...>` keyed by ontology ID. All methods
  accept `ontologyId` as first parameter. Backward-compatible single-ontology methods
  preserved.
- **`aberowlapi/src/ReasonerFactory.groovy`** (new): Factory creating ELK (default),
  StructuralReasoner, or HermiT based on a string identifier. Extensible for future
  reasoners.
- **`aberowlapi/OntologyServer.groovy`**: Supports three startup modes: single OWL file,
  directory of `.owl` files, or JSON config file listing ontologies with reasoner types.
  HermiT `@Grab` dependency added. Parallel classification via GParsPool.
- **All servlets updated**: `runQuery`, `findRoot`, `getObjectProperties`,
  `retrieveRSuccessors`, `retrieveAllLabels`, `getStatistics`, `getSparqlExamples`,
  `runSparqlQuery`, `reloadOntology`, `updateOntology`, `health`. All accept
  `ontologyId` parameter. Auto-resolve to default when only one ontology loaded.
- **New servlets**: `addOntology.groovy`, `removeOntology.groovy`,
  `listLoadedOntologies.groovy` for dynamic runtime management.
- **`docker-compose.yml`**: Updated to support both single-ontology and multi-ontology
  modes via `ONTOLOGY_PATH`, `CONTAINER_ID`, `REASONER_TYPE` env vars. Memory limits
  added.
- **`server_manager.py`**: Fixed registration to use `ONTOLOGY_ID` env var instead of
  deriving from filename.

### Phase 2: Bug fixes
- Fixed double-URL bug (`/api/api/` → `/api/`) in `search_all` and `dlquery_all`.
- Rewrote `search_all` to query central Elasticsearch directly with boosted `dis_max`
  query (oboid=10000, label=100, synonym=75), eliminating slow scatter-gather.
- Fixed circular import in `updater.py`.
- Fixed `reloadOntology.groovy` memory leak (now calls `disposeOntology` before reload).

### Phase 3: Missing API endpoints
- `GET /api/queryNames`: boosted class search via central ES.
- `GET /api/getClass`: class detail from ES with fallback to worker API.
- `GET /api/listOntologies`, `GET /api/getOntology`: ontology metadata.
- `GET /api/queryOntologies`: search ontology metadata.
- `GET /api/getStats`, `GET /api/getStatuses`: statistics and status.

### Phase 4: SPARQL query expansion
- `runSparqlQuery.groovy`: Added `FILTER OWL(?var, type, "dl_query")` pattern alongside
  existing `VALUES` pattern.
- `central_server/app/sparql_expander.py` (new): Expansion engine for central server.
- `POST /api/sparql`: central SPARQL expansion endpoint. Parses SPARQL for OWL patterns,
  dispatches DL queries to correct worker, rewrites SPARQL, executes against Virtuoso.

### Phase 5: MCP servers
- **`central_server/mcp_ontology_server.py`** (new): 6 tools (list_ontologies,
  search_classes, run_dl_query, get_class_info, get_ontology_info, browse_hierarchy).
  Rich Manchester OWL Syntax documentation in tool descriptions. Official `mcp` SDK,
  streamable HTTP transport.
- **`central_server/mcp_sparql_server.py`** (new): 3 tools (expand_sparql,
  list_sparql_examples, explain_expansion). Teaches agents the VALUES/FILTER OWL
  expansion patterns.
- Old custom WebSocket `mcp_server.py` replaced.

### Phase 6: Authentication
- **`central_server/app/auth.py`** (new): API key management (Redis), rate limit key
  generation.
- Rate limiting via `slowapi` (60/min public, 600/min with API key).
- Admin endpoints: `POST/GET/DELETE /admin/api_keys`.
- Webhook update trigger: `POST /api/webhook/update/{ontology_id}`.

### Phase 7: Update strategy
- `POST /api/webhook/update/{ontology_id}`: push-based update trigger with webhook
  secret verification.
- Preserved the daily pull workflow.

### Phase 8: Frontend
- **React 19 + TypeScript SPA** with Vite, TailwindCSS 4, CodeMirror.
- Pages: Home (search + stats + ontology table), Search Results, Ontology Browser
  (hierarchy tree + DL query + metadata tabs), Class Detail, DL Query, SPARQL Playground.
- Components: Layout, ClassCard, TreeNode (recursive lazy-loading hierarchy).
- Build output: `central_server/app/static/dist/` served by FastAPI catch-all route.

### Phase 9: Testing
- 56 unit tests (no Docker): SPARQL expander, auth, MCP servers, central API.
- 6 live browsing tests against beta.aber-owl.net.
- 24 Docker integration tests (existing, for ontology servlets).
- All tests pass.

### Phase 10: Deployment
- **Live at https://beta.aber-owl.net** with HTTPS (Let's Encrypt).
- Three-tier reverse proxy for the hosted deployment.
- Central stack: FastAPI + Redis + Elasticsearch + Virtuoso.
- 14 worker containers hosting 81+ ontologies, 896,000+ classes.
- See `deploy/README.md` for full deployment documentation.

---

## Previous changes

### Centralised infrastructure and ontology intake (prior work)

1. **Centralised Virtuoso and Elasticsearch**: one shared instance each.
2. **Automated ontology intake**: daily discovery from OBOFoundry and BioPortal.
3. **Hot-swap update workflow**: ontologies updated without downtime.
4. **Admin web interface**: Bootstrap dashboard for monitoring.
5. **Per-ontology stack simplification**: Groovy API + nginx only per container.
