"""
AberOWL 1 API compatibility layer.

AberOWL 2 changed the API paths. The old ones do not 404 — the SPA catch-all
serves `index.html`, so every v1 path answers HTTP 200 with a web page and
consumers parse garbage. That is why the API looked removed from outside
(biopragmatics/bioregistry#2030).

This router serves the AberOWL 1 surface at its original paths, backed by
AberOWL 2 internals. The v2 endpoints remain the canonical, documented API; this
is a translation layer so existing consumers keep working without shipping a
change. See issue #94.

The contract is not reconstructed from memory. It comes from two artifacts:

  * `aberowlweb/static/openapi/schema.yml` — the OpenAPI spec the old Django app
    served at its own /docs, which declares twelve operations.
  * a real archived response, committed at
    `tests/fixtures/aberowl_v1_ontology_list.json`.

Two details of that contract are easy to get wrong and are load-bearing:

  * `acronym` is UPPERCASE. Bioregistry keys its records by it.
  * v1 returned HTTP 200 with an `{"status": "error", ...}` envelope rather than
    a 4xx. Clients branch on the envelope, so we reproduce it rather than
    "improving" it.

This module holds the registry- and Elasticsearch-backed operations only. The
ones that need a worker (dlquery, root, objectproperty, _matchsuperclasses) and
the retired ones follow separately.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter()

# v1 reported the *reasoner* outcome here (Classified / Incoherent / Unloadable /
# Unknown), not a serving state. AberOWL 2 tracks the reasoner outcome per
# ontology inside each worker but never propagates it to the central registry, so
# we cannot state it yet. "Unknown" is one of v1's own values; inferring
# "Classified" from a worker being online would be a fabricated claim about
# reasoning. Plumbing the real value through is a separate step.
DEFAULT_REASONER_STATUS = "Unknown"


def _deps():
    """Fetch the live redis/ES handles.

    Imported lazily: `app.main` imports this module, so a module-level import
    would be circular. The handles are also module globals assigned during
    startup, so they must be read at call time rather than bound at import.
    """
    from app import main as _main

    return _main.redis_client, _main.es_mgr


def _err(message: str) -> Dict[str, Any]:
    """v1's error envelope. Note it rode on HTTP 200; clients branch on `status`."""
    return {"status": "error", "message": message}


async def _registry_entries() -> List[Dict[str, Any]]:
    redis_client, _ = _deps()
    from app import main as _main

    raw = await redis_client.hvals(_main.REGISTRY_KEY)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


def _submission(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The v1 `submission` object, filled from what the v2 registry actually holds.

    Fields v2 has no equivalent for are emitted as null rather than invented —
    v1 emitted nulls for many of these too. `download_url` stays null until the
    central server serves the corpus (see #95); a wrong URL would be worse than
    an absent one, since Bioregistry prefixes it with the site root.
    """
    return {
        "id": None,
        "submission_id": None,
        "download_url": None,
        "domain": None,
        "description": entry.get("description") or None,
        "documentation": entry.get("documentation") or None,
        "publication": entry.get("publication") or None,
        "publications": None,
        "products": None,
        "taxon": None,
        "date_released": None,
        "date_created": None,
        "home_page": entry.get("home_page") or entry.get("homepage") or None,
        "version": entry.get("version_info") or None,
        "has_ontology_language": "OWL",
        "nb_classes": entry.get("class_count"),
        "nb_individuals": entry.get("individual_count"),
        "nb_properties": entry.get("property_count"),
        "max_depth": None,
        "max_children": None,
        "avg_children": None,
        "classifiable": None,
        "nb_inconsistent": None,
        "indexed": None,
        "md5sum": entry.get("source_md5") or None,
    }


def _ontology_record(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One entry in the v1 `/api/ontology/` list."""
    acronym = (entry.get("ontology") or entry.get("ontology_id") or "").upper()
    return {
        "acronym": acronym,
        "name": entry.get("title") or entry.get("name") or acronym,
        "status": DEFAULT_REASONER_STATUS,
        "topics": None,
        "species": None,
        "submission": _submission(entry),
    }


# ---------------------------------------------------------------------------
# GET /api/ontology/   — the endpoint Bioregistry harvests
# ---------------------------------------------------------------------------

@router.get("/api/ontology/")
@router.get("/api/ontology")
async def v1_list_ontologies():
    """v1 returned a bare JSON list (DRF ListAPIView), not an envelope."""
    entries = await _registry_entries()
    records = [_ontology_record(e) for e in entries if e.get("ontology") or e.get("ontology_id")]
    records.sort(key=lambda r: r["acronym"])
    return records


# ---------------------------------------------------------------------------
# GET /api/ontology/_find
# ---------------------------------------------------------------------------

@router.get("/api/ontology/_find")
async def v1_find_ontology(query: Optional[str] = Query(None)):
    """v1 returned a bare list of ES `_source` docs, sorted by name length."""
    if query is None:
        return _err("query field is required")
    _, es_mgr = _deps()
    hits = await es_mgr.search_ontologies(query)
    hits.sort(key=lambda h: len(h.get("name") or ""))
    return hits


# ---------------------------------------------------------------------------
# GET /api/class/_find
# ---------------------------------------------------------------------------

@router.get("/api/class/_find")
async def v1_find_class(
    query: Optional[str] = Query(None),
    ontology: Optional[str] = Query(None),
):
    if query is None:
        return _err("Please provide query parameter!")
    _, es_mgr = _deps()
    results = await es_mgr.search_classes(query, ontology=ontology, size=100)
    return {"status": "ok", "result": results}


# ---------------------------------------------------------------------------
# GET /api/class/_startwith
# ---------------------------------------------------------------------------

@router.get("/api/class/_startwith")
async def v1_class_startswith(
    query: Optional[str] = Query(None),
    ontology: Optional[str] = Query(None),
):
    """v1 required `ontology` here, and sorted results by label length."""
    if query is None:
        return _err("query is required")
    if ontology is None:
        return _err("ontology is required")
    _, es_mgr = _deps()
    results = await es_mgr.search_classes(query, ontology=ontology, prefix=True, size=100)

    def _label_len(doc: Dict[str, Any]) -> int:
        label = doc.get("label")
        if isinstance(label, list):
            label = label[0] if label else ""
        return len(label or "")

    results.sort(key=_label_len)
    return {"status": "ok", "result": results}
