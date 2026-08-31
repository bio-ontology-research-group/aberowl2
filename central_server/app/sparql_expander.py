"""
SPARQL Query Rewriting with embedded OWL DL frames.

AberOWL rewrites SPARQL queries that contain OWL DL frames into plain
SPARQL with concrete IRIs spliced in. AberOWL never executes SPARQL;
the caller runs the rewritten query against any endpoint they choose
(Ontobee, UniProt, Wikidata, …).

Two embedded frame patterns are supported:

1. VALUES pattern:
   VALUES ?var { OWL <type> <ontology_id> { dl_query } }
   ⇒  VALUES ?var { <iri1> <iri2> ... }

2. FILTER pattern:
   FILTER OWL(?var, <type>, <ontology_id>, "dl_query")
   ⇒  FILTER (?var IN (<iri1>, <iri2>, ...))

`type` is one of: subclass, superclass, equivalent, subeq, supeq.
`ontology_id` is the registered ontology id (case-insensitive; may
contain letters, digits, `_`, `-`, or `.`, e.g. `go-plus`, `chebi.ext`).

DL resolution is dispatched to the worker that has the ontology loaded,
using the same registry the rest of the central server uses.

Per-frame errors (unknown ontology, offline worker, DL parse error,
HTTP failure) do not abort the whole rewrite. The frame is replaced
with an empty IRI list (so the SPARQL stays syntactically valid) and a
structured error is returned alongside the rewritten query.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Ontology id may contain letters, digits, underscore, hyphen, or dot.
_ONT_ID = r"[\w.\-]+"
_QUERY_TYPE = r"(?:subclass|superclass|equivalent|subeq|supeq)"

VALUES_OWL_PATTERN = re.compile(
    rf"VALUES\s+(\?\w+)\s*\{{\s*OWL\s+({_QUERY_TYPE})\s+({_ONT_ID})\s*\{{\s*(.*?)\s*\}}\s*\}}",
    re.IGNORECASE | re.DOTALL,
)
FILTER_OWL_PATTERN = re.compile(
    rf"""FILTER\s+OWL\(\s*(\?\w+)\s*,\s*({_QUERY_TYPE})\s*,\s*({_ONT_ID})\s*,\s*["'](.+?)["']\s*\)""",
    re.IGNORECASE,
)

# --- AberOWL 1 frame syntax -------------------------------------------------
# v1 embedded the target SPARQL endpoint in the frame itself, between the query
# type and the ontology:
#
#     VALUES ?x { OWL subeq <https://sparql.uniprot.org/sparql> <GO> { 'cell death' } }
#
# v1 rewrote the frame and then 302-redirected the caller to that endpoint (#102).
# The two forms cannot collide: v2's ontology group is [\w.\-]+, which never
# matches a leading "<".
#
# `realize` was a v1 query type. It is accepted so the frame is recognised rather
# than silently ignored; if a worker rejects it, the per-frame error path reports
# that, which is more useful than not matching at all.
_QUERY_TYPE_V1 = r"(?:subclass|superclass|equivalent|subeq|supeq|realize)"
_V1_ENDPOINT = r"[A-Za-z][\w+.\-]*://[^\s>]+"

VALUES_OWL_V1_PATTERN = re.compile(
    rf"VALUES\s+(\?\w+)\s*\{{\s*OWL\s+({_QUERY_TYPE_V1})\s+<({_V1_ENDPOINT})>\s+<({_ONT_ID})>\s*"
    rf"\{{\s*(.*?)\s*\}}\s*\}}",
    re.IGNORECASE | re.DOTALL,
)
FILTER_OWL_V1_PATTERN = re.compile(
    rf"FILTER\s*\(\s*(\?\w+)\s+in\s+\(\s*OWL\s+({_QUERY_TYPE_V1})\s+<({_V1_ENDPOINT})>\s+<({_ONT_ID})>\s*"
    rf"\{{\s*(.*?)\s*\}}\s*\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def find_v1_service_endpoint(sparql: str) -> Optional[str]:
    """The SPARQL endpoint named inside an AberOWL 1 frame, if there is one.

    Returns None for a v2-style query, which carries no endpoint. The caller
    uses this to choose between v1's redirect and v2's JSON response.
    """
    for pattern in (VALUES_OWL_V1_PATTERN, FILTER_OWL_V1_PATTERN):
        m = pattern.search(sparql)
        if m:
            return m.group(3)
    return None



async def _run_dl_query(
    server_url: str,
    ontology_id: str,
    dl_query: str,
    query_type: str,
    timeout: int = 30,
) -> Tuple[List[str], str | None]:
    """Run a DL query against a worker and return (iris, error).

    On success: ([...iris...], None).
    On failure: ([], "<error message>").
    """
    api_url = f"{server_url.rstrip('/')}/api/runQuery.groovy"
    params = {
        "query": dl_query,
        "type": query_type,
        "labels": "false",
        "ontologyId": ontology_id.lower(),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                api_url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and data.get("error"):
                        return [], str(data.get("message") or data.get("error"))
                    results = data.get("result", []) if isinstance(data, dict) else []
                    iris = [item["class"] for item in results if isinstance(item, dict) and "class" in item]
                    return iris, None
                text = await resp.text()
                logger.warning("DL query to %s failed (%s): %s", api_url, resp.status, text[:200])
                return [], f"worker returned HTTP {resp.status}"
    except Exception as e:
        logger.warning("DL query error for %s: %s", ontology_id, e)
        return [], f"worker unreachable: {e}"


async def expand_sparql_query(
    sparql: str,
    server_lookup: Dict[str, str],
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rewrite OWL DL frames in a SPARQL query.

    Args:
        sparql: SPARQL query with embedded OWL frames.
        server_lookup: dict mapping lowercased ontology_id -> worker base URL
            (only ontologies whose worker is online).

    Returns:
        (rewritten_query, expansions, errors)

        - rewritten_query: SPARQL string with each OWL frame replaced.
          Frames whose DL resolution fails are still replaced (with an
          empty IRI list) so the result remains syntactically valid.
        - expansions: per-frame info for successful resolutions
          (pattern, variable, ontology, type, dl_query, result_count).
        - errors: per-frame error objects
          (pattern, variable, ontology, type, dl_query, error).
    """
    expanded = sparql
    expansions: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    async def resolve(variable: str, query_type: str, ontology_id: str, dl_query: str):
        server_url = server_lookup.get(ontology_id.lower())
        if not server_url:
            return None, f"ontology '{ontology_id}' is not registered or its worker is offline"
        iris, err = await _run_dl_query(server_url, ontology_id, dl_query, query_type)
        if err is not None:
            return None, err
        return iris, None

    # Process AberOWL 1 frames first. They carry an endpoint the v2 patterns do
    # not, and the caller redirects to it (#102); the rewriting itself is the
    # same, so the endpoint group is simply not used here.
    for match in list(VALUES_OWL_V1_PATTERN.finditer(sparql)):
        variable, query_type, ontology_id, dl_query = (
            match.group(1), match.group(2), match.group(4), match.group(5).strip())
        iris, err = await resolve(variable, query_type, ontology_id, dl_query)
        entry = {"pattern": "VALUES(v1)", "variable": variable, "ontology": ontology_id,
                 "type": query_type, "dl_query": dl_query, "endpoint": match.group(3)}
        if err is not None:
            replacement = f"VALUES {variable} {{ }}"
            errors.append({**entry, "error": err})
        else:
            iri_list = " ".join(f"<{iri}>" for iri in iris)
            replacement = f"VALUES {variable} {{ {iri_list} }}"
            expansions.append({**entry, "result_count": len(iris)})
        expanded = expanded.replace(match.group(0), replacement, 1)

    for match in list(FILTER_OWL_V1_PATTERN.finditer(sparql)):
        variable, query_type, ontology_id, dl_query = (
            match.group(1), match.group(2), match.group(4), match.group(5).strip())
        iris, err = await resolve(variable, query_type, ontology_id, dl_query)
        entry = {"pattern": "FILTER(v1)", "variable": variable, "ontology": ontology_id,
                 "type": query_type, "dl_query": dl_query, "endpoint": match.group(3)}
        if err is not None:
            replacement = f"FILTER({variable} IN ())"
            errors.append({**entry, "error": err})
        else:
            iri_list = ", ".join(f"<{iri}>" for iri in iris)
            replacement = f"FILTER({variable} IN ({iri_list}))"
            expansions.append({**entry, "result_count": len(iris)})
        expanded = expanded.replace(match.group(0), replacement, 1)

    # Process VALUES patterns
    for match in list(VALUES_OWL_PATTERN.finditer(sparql)):
        variable, query_type, ontology_id, dl_query = match.group(1), match.group(2), match.group(3), match.group(4).strip()
        iris, err = await resolve(variable, query_type, ontology_id, dl_query)
        if err is not None:
            replacement = f"VALUES {variable} {{ }}"
            errors.append({
                "pattern": "VALUES",
                "variable": variable,
                "ontology": ontology_id,
                "type": query_type,
                "dl_query": dl_query,
                "error": err,
            })
        else:
            iri_list = " ".join(f"<{iri}>" for iri in iris)
            replacement = f"VALUES {variable} {{ {iri_list} }}"
            expansions.append({
                "pattern": "VALUES",
                "variable": variable,
                "ontology": ontology_id,
                "type": query_type,
                "dl_query": dl_query,
                "result_count": len(iris),
            })
        expanded = expanded.replace(match.group(0), replacement, 1)

    # Process FILTER patterns
    for match in list(FILTER_OWL_PATTERN.finditer(expanded)):
        variable, query_type, ontology_id, dl_query = match.group(1), match.group(2), match.group(3), match.group(4).strip()
        iris, err = await resolve(variable, query_type, ontology_id, dl_query)
        if err is not None:
            # Empty IN list is illegal in standard SPARQL; use a never-matching guard.
            replacement = f"FILTER (false)"
            errors.append({
                "pattern": "FILTER",
                "variable": variable,
                "ontology": ontology_id,
                "type": query_type,
                "dl_query": dl_query,
                "error": err,
            })
        else:
            if iris:
                iri_list = ", ".join(f"<{iri}>" for iri in iris)
                replacement = f"FILTER ({variable} IN ({iri_list}))"
            else:
                replacement = f"FILTER (false)"
            expansions.append({
                "pattern": "FILTER",
                "variable": variable,
                "ontology": ontology_id,
                "type": query_type,
                "dl_query": dl_query,
                "result_count": len(iris),
            })
        expanded = expanded.replace(match.group(0), replacement, 1)

    return expanded, expansions, errors
