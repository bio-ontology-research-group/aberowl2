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
- [ ] **Content negotiation** — return JSON-LD and RDF (Turtle) for class and
      ontology records based on the `Accept` header, alongside the current JSON.
- [ ] **Persistent-identifier resolution** — a stable endpoint that resolves any
      served IRI or CURIE to its record under a documented URL scheme.
- [ ] **Machine-readable dataset metadata** — a DCAT / schema.org description of
      the repository and of each ontology (title, version, license, publisher,
      distribution), so the corpus is discoverable by data catalogues.
- [ ] **Versioned, dated records** — surface the ontology version and
      last-updated date in each record.
- [ ] **Service descriptor** — a `/.well-known` or `/fair` document listing the
      FAIR endpoints and supported representations.

## Notes
- Build on the existing FastAPI central server (`app/main.py`) and the registry
  metadata (`/api/getOntology` already carries version + license).
- Keep it **additive**: the current REST and MCP endpoints are unchanged.
- Update `central_server/README.md` and the paper's FAIR section when this lands.
