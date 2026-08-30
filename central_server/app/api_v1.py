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

This module holds the registry-, Elasticsearch- and worker-backed operations.
The retired ones (`_similar`, `dlquery/logs`) and `/api/sparql` follow
separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import APIRouter, Body, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter()

# v1 reported the *reasoner* outcome here (Classified / Incoherent / Unloadable /
# Unknown), not a serving state. AberOWL 2 tracks the reasoner outcome per
# ontology inside each worker but never propagates it to the central registry, so
# we cannot state it yet. "Unknown" is one of v1's own values; inferring
# "Classified" from a worker being online would be a fabricated claim about
# reasoning. Plumbing the real value through is a separate step.
DEFAULT_REASONER_STATUS = "Unknown"

WORKER_TIMEOUT = 30

# v1 paginated `offset` results at a time (its DEFUALT_PAGE_SIZE). Kept so a
# caller looping over pages sees the same page boundaries it used to.
V1_PAGE_SIZE = 10

# `/service/api/` was AberOWL 1's public passthrough to the reasoner servlets.
# BARTOC still lists it as *the* AberOWL API, and the old frontend's help page
# documented it, so it is restored — but strictly read-only. The mutating
# servlets (addOntology, removeOntology, updateOntology, reloadOntology,
# triggerIndexing) and the raw Elasticsearch proxy must never be reachable this
# way, whatever a caller asks for.
SERVICE_API_ALLOWED = frozenset({
    "runQuery.groovy",
    "queryNames.groovy",
    "findRoot.groovy",
    "getObjectProperties.groovy",
    "getStatistics.groovy",
    "retrieveAllLabels.groovy",
    "retrieveRSuccessors.groovy",
    "getSparqlExamples.groovy",
    "health.groovy",
})


def _deps():
    """Fetch the live redis/ES handles.

    Imported lazily: `app.main` imports this module, so a module-level import
    would be circular. The handles are also module globals assigned during
    startup, so they must be read at call time rather than bound at import.
    """
    from app import main as _main

    return _main.redis_client, _main.es_mgr


def _fix_iri(iri: str) -> str:
    """Restore a scheme mangled by path normalisation.

    Proxies and routers collapse `http://` in a path segment to `http:/`.
    AberOWL 1 repaired it the same way (`fix_iri_path_param`), and links in the
    wild carry both forms.
    """
    iri = re.sub(r"(?!http:\/\/)(http:\/){1}", "http://", iri)
    iri = re.sub(r"(?!https:\/\/)(https:\/){1}", "https://", iri)
    return iri


async def _worker_for(ontology_id: str) -> Optional[Dict[str, Any]]:
    """The online worker hosting an ontology, matched case-insensitively.

    v1 acronyms are uppercase and registry ids are lowercase, so a
    case-sensitive match here would select nothing for every legacy caller.
    """
    wanted = (ontology_id or "").strip().lower()
    for entry in await _registry_entries():
        if (entry.get("ontology") or "").lower() != wanted:
            continue
        if entry.get("status") != "online" or not entry.get("url"):
            return None
        return entry
    return None


async def _call_worker(
    server: Dict[str, Any], script: str, params: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """GET one worker servlet. Returns None on any failure; callers map that to
    v1's error envelope rather than leaking a traceback."""
    url = f"{str(server.get('url')).rstrip('/')}/api/{script}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=WORKER_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    logger.warning("v1: %s returned %s for %s", script, resp.status, params)
                    return None
                return await resp.json(content_type=None)
    except Exception as e:
        logger.error("v1: %s failed for %s: %s", script, params, e)
        return None


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


# ---------------------------------------------------------------------------
# GET /api/dlquery   — used in production by genome-linter and pheno-agent
# ---------------------------------------------------------------------------

@router.get("/api/dlquery")
async def v1_dlquery(
    query: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    ontology: Optional[str] = Query(None),
    axioms: Optional[str] = Query(None),
    labels: Optional[str] = Query(None),
    direct: str = Query("true"),
    offset: Optional[str] = Query(None),
):
    """Run a DL query against one ontology, or across all of them.

    `offset` is a 1-based page number, as in v1, and pages are V1_PAGE_SIZE
    long. v1 backed this with a Paginator cached per (query, type) in module
    state; that cache is not reproduced — it is wrong for a service running more
    than one worker process — so the query re-runs and the result is sliced.
    Slower per page, but stateless and correct.

    Ignoring the parameter instead would be worse than not supporting it: a
    caller looping over pages until one comes back empty would receive the full
    result set every time and never terminate.
    """
    if query is None:
        return _err("query is required")
    if type is None:
        return _err("type is required")

    params = {
        "query": query,
        "type": type,
        "direct": direct,
        "labels": labels if labels is not None else "false",
        "axioms": axioms if axioms is not None else "false",
    }

    if ontology:
        server = await _worker_for(ontology)
        if server is None:
            return {"status": "exception",
                    "message": f"Ontology '{ontology}' does not exist or its server is down"}
        params["ontologyId"] = server.get("ontology")
        data = await _call_worker(server, "runQuery.groovy", params)
        if data is None:
            return {"status": "exception", "message": "API server is down!"}
        results = data.get("result", [])
    else:
        # v1 fanned out when no ontology was named.
        entries = [e for e in await _registry_entries()
                   if e.get("status") == "online" and e.get("url")]
        if not entries:
            return {"status": "exception", "message": "API server is down!"}

        async def one(entry):
            p = dict(params, ontologyId=entry.get("ontology"))
            data = await _call_worker(entry, "runQuery.groovy", p)
            out = (data or {}).get("result", []) or []
            for item in out:
                if isinstance(item, dict):
                    item.setdefault("ontology", entry.get("ontology"))
            return out

        gathered = await asyncio.gather(*(one(e) for e in entries))
        results = [item for sub in gathered for item in sub]

    total = len(results)
    if offset is not None:
        try:
            page = int(offset)
        except (TypeError, ValueError):
            return _err("offset must be an integer page number")
        if page < 1:
            return _err("offset must be a page number of 1 or greater")
        start = (page - 1) * V1_PAGE_SIZE
        results = results[start:start + V1_PAGE_SIZE]

    return {"status": "ok", "result": results, "total": total}


# ---------------------------------------------------------------------------
# GET /api/ontology/{acronym}/root/{class_iri}
# ---------------------------------------------------------------------------

@router.get("/api/ontology/{acronym}/root/{class_iri:path}")
async def v1_find_root(acronym: str, class_iri: str):
    server = await _worker_for(acronym)
    if server is None:
        return {"status": "exception",
                "message": f"Ontology '{acronym}' does not exist or its server is down"}
    data = await _call_worker(
        server, "findRoot.groovy",
        {"query": _fix_iri(class_iri), "ontologyId": server.get("ontology")},
    )
    if data is None:
        return {"status": "exception", "message": "API server is down!"}
    result = data.get("result", [])
    return {"status": "ok", "result": result, "total": len(result)}


# ---------------------------------------------------------------------------
# GET /api/ontology/{acronym}/objectproperty[/{property_iri}]
# ---------------------------------------------------------------------------

@router.get("/api/ontology/{acronym}/objectproperty")
@router.get("/api/ontology/{acronym}/objectproperty/")
async def v1_object_properties(acronym: str):
    server = await _worker_for(acronym)
    if server is None:
        return {"status": "exception",
                "message": f"Ontology '{acronym}' does not exist or its server is down"}
    data = await _call_worker(
        server, "getObjectProperties.groovy", {"ontologyId": server.get("ontology")}
    )
    if data is None:
        return {"status": "exception", "message": "API server is down!"}
    result = data.get("result", [])
    return {"status": "ok", "result": result, "total": len(result)}


@router.get("/api/ontology/{acronym}/objectproperty/{property_iri:path}")
async def v1_object_property(acronym: str, property_iri: str):
    server = await _worker_for(acronym)
    if server is None:
        return {"status": "exception",
                "message": f"Ontology '{acronym}' does not exist or its server is down"}
    data = await _call_worker(
        server, "getObjectProperties.groovy",
        {"ontologyId": server.get("ontology"), "property": _fix_iri(property_iri)},
    )
    if data is None:
        return {"status": "exception", "message": "API server is down!"}
    out = dict(data)
    out["status"] = "ok"
    return out


# ---------------------------------------------------------------------------
# POST /api/ontology/{acronym}/class/_matchsuperclasses
#   Used in production by phenotype-reactor.
# ---------------------------------------------------------------------------

@router.post("/api/ontology/{acronym}/class/_matchsuperclasses")
async def v1_match_superclasses(acronym: str, payload: Dict[str, Any] = Body(...)):
    """Superclasses shared by the source and target sets, minus any that are
    themselves a superclass of another member — i.e. the most specific common
    ancestors.

    This was pure orchestration in the v1 Django layer over repeated
    `superclass` DL queries (ont_server_request_processor.match_superclasses),
    so it is reproduced here rather than pushed into a worker.
    """
    source_classes = payload.get("source_classes")
    target_classes = payload.get("target_classes")
    if source_classes is None:
        return {"status": "exception", "message": "'source_classes' element is required"}
    if target_classes is None:
        return {"status": "exception", "message": "'target_classes' element is required"}

    server = await _worker_for(acronym)
    if server is None:
        return {"status": "exception",
                "message": f"Ontology '{acronym}' does not exist or its server is down"}

    async def superclasses_of(expr: str) -> List[Dict[str, Any]]:
        data = await _call_worker(
            server, "runQuery.groovy",
            {"query": expr, "type": "superclass", "direct": "false",
             "labels": "false", "axioms": "false",
             "ontologyId": server.get("ontology")},
        )
        return (data or {}).get("result", []) or []

    supercls_map: Dict[str, Dict[str, Any]] = {}
    for cls in list(source_classes) + list(target_classes):
        for sup in await superclasses_of(f"<{cls}>"):
            key = sup.get("owlClass")
            if key and key not in supercls_map:
                supercls_map[key] = sup

    # Drop any ancestor that is itself an ancestor of another member, leaving
    # only the most specific ones.
    for key in list(supercls_map):
        for sup in await superclasses_of(key):
            supercls_map.pop(sup.get("owlClass"), None)

    return {"result": list(supercls_map.values())}


# ---------------------------------------------------------------------------
# GET /service/api/{script}   — AberOWL 1's public reasoner passthrough
# ---------------------------------------------------------------------------

@router.get("/service/api/{script}")
async def v1_service_api(script: str, request: Request):
    """Forward a read-only reasoner servlet to the worker hosting the ontology.

    Only the servlets in SERVICE_API_ALLOWED are reachable. Anything else is
    refused rather than proxied — the mutating servlets would otherwise be
    exposed unauthenticated to the internet.
    """
    if script not in SERVICE_API_ALLOWED:
        return {"status": "error",
                "message": f"'{script}' is not available through this endpoint"}

    params = dict(request.query_params)
    ontology = params.get("ontology") or params.get("ontologyId")
    if not ontology:
        return _err("ontology is required")

    server = await _worker_for(ontology)
    if server is None:
        return {"status": "exception",
                "message": f"Ontology '{ontology}' does not exist or its server is down"}

    params["ontologyId"] = server.get("ontology")
    data = await _call_worker(server, script, params)
    if data is None:
        return {"status": "exception", "message": "API server is down!"}
    return data
