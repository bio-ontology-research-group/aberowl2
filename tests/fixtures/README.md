# Test fixtures

## `aberowl_v1_ontology_list.json`

A 20-entry sample of a **real AberOWL 1 response**, used as the golden contract
for the AberOWL 1 compatibility work (`GET /api/ontology/?format=json`).

**Provenance.** Internet Archive snapshot `20221120122151` of
`http://aber-owl.net/api/ontology/?drf_fromat=json&format=json`, retrieved
2026-08-27:

```
https://web.archive.org/web/20221120122151id_/http://aber-owl.net/api/ontology/?drf_fromat=json&format=json
```

The full archived response holds 1,374 entries (1.2 MB). This file contains 20 manually selected entries,
sorted by acronym, with their original fields. The selection excludes link-spam
submissions from the archived registry, which also motivate the `SKIP` set in
Bioregistry's getter. The sample contains:

- the eight ontologies the tests name: `CHEBI`, `DOID`, `FMA`, `GO`, `HP`,
  `MONDO`, `PATO`, `UBERON`;
- eight further real ontologies with a `submission` object: `AEO`, `BTO`,
  `CL`, `ENVO`, `NCIT`, `OBI`, `PO`, `SO`;
- four whose `submission` is `null`: `CSTD`, `FRAPO`, `IBO`, `TUNIGO-SLIM`,
  so the contract test sees both shapes.

To regenerate or extend it, re-fetch the archive URL in “Provenance” and pick by acronym.

**What it pins down.** The shape Bioregistry's getter reads
(`bioregistry/external/aberowl/__init__.py`): `acronym`, `name`, `status`, and
`submission.{home_page, description, version, download_url}`. Two details that
are easy to get wrong and are visible here: `acronym` is **uppercase**, and
`status` carries the *reasoner* outcome (`Classified` / `Incoherent` /
`Unloadable` / `Unknown`), not a serving state.
