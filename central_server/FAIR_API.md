# FAIR API for AberOWL 2.0

Goal: expose AberOWL 2.0's ontologies, classes, and reasoning results through an
interface aligned with the **FAIR** principles (Findable, Accessible,
Interoperable, Reusable), formalizing access that is today only partly available
through the REST API and the MCP server.

> Status: **design / WIP** (this PR). Implementation follows; checklist below.

## What AberOWL 2.0 already provides (FAIR-aligned)
- **Findable** — every ontology and class has a persistent IRI; classes are
  searchable through `/api/search_all`; the corpus is listed by
  `/api/listOntologies`; the web UI gives deep-linkable pages per ontology / class.
- **Accessible** — an open REST API and an MCP server serve ontologies, class
  metadata, and reasoning results over HTTP; a class is retrievable by IRI
  (`/api/getClass`, `/api/resolve`); the code is BSD-licensed.
- **Interoperable** — classes use standard OWL IRIs; the SPARQL rewriter lets a
  class expression drive federated queries against external endpoints.
- **Reusable** — `/api/getOntology` returns descriptive metadata (title,
  description, version, class/property counts, license, classification status).

## What this PR adds (the gap to a formal FAIR API)
- [x] **Machine-readable dataset metadata** — a MOD/DCAT/Hydra JSON-LD
      semantic-artefact catalogue is live at `/artefacts` (462 artefacts, per-ontology
      records with distributions, `/records`, `/resources`). OntoPortal-compatible.
- [x] **Content negotiation** — `/artefacts*`, `/records*`, and `/fair` now serve
      JSON-LD (default), **Turtle** (`Accept: text/turtle` or `?format=ttl`), and
      **RDF/XML** (`application/rdf+xml` or `?format=rdfxml`), via rdflib. The
      `format` params previously accepted `ttl`/`rdfxml` but returned JSON.
- [x] **Versioned, dated records** — `dcterms:issued`/`modified` now come from real
      registry metadata (`version_info` for issued, `source_last_modified` /
      `last_indexed` for modified) instead of a request-time `datetime.utcnow()`
      stamp; a field is omitted when no real date exists (never fabricated).
- [x] **Service descriptor** — `GET /fair` lists the FAIR endpoints, RDF
      representations, and the vocabularies conformed to (MOD/DCAT/Hydra), so a
      client or a FAIR assessor (O'FAIRe) can discover them from one document.
- [~] **Persistent-identifier resolution** — each artefact resolves at
      `/artefacts/{id}` and each class via `/api/resolve`; genuinely persistent IDs
      still need **HTTPS** (ops) and, ideally, a registered PID scheme.

## Remaining (not code)
- **HTTPS** on the public host, so the resolvable IDs are `https` and access is secure.
- Validate the live service with **O'FAIRe** and record the score.

## Notes
- Build on the existing FastAPI central server (`app/main.py`) and the registry
  metadata (`/api/getOntology` already carries version + license).
- Keep it **additive**: the current REST and MCP endpoints are unchanged.
- Update `central_server/README.md` and the paper's FAIR section when this lands.
