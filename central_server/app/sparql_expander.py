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
from typing import Any, Dict, List, Tuple

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
