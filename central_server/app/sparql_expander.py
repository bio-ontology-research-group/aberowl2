"""
SPARQL Query Expansion with embedded OWL DL queries.

Supports two expansion patterns:

1. VALUES pattern:
   VALUES ?var { OWL type ontology { dl_query } }
   Expands to: VALUES ?var { <iri1> <iri2> ... }

2. FILTER pattern:
   FILTER OWL(?var, type, ontology, "dl_query")
   Expands to: FILTER (?var IN (<iri1>, <iri2>, ...))

The DL query is dispatched to the appropriate ontology container,
and the results (class IRIs) replace the expansion pattern in the SPARQL query.
The expanded query is then executed against the central Virtuoso SPARQL endpoint.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote as url_quote

import aiohttp

logger = logging.getLogger(__name__)

# Regex patterns for OWL expansion
VALUES_OWL_PATTERN = re.compile(
    r"VALUES\s+(\?\w+)\s*\{\s*OWL\s+(\w+)\s+(\w+)\s*\{\s*(.*?)\s*\}\s*\}",
    re.IGNORECASE | re.DOTALL,
)
FILTER_OWL_PATTERN = re.compile(
    r"""FILTER\s+OWL\(\s*(\?\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*["'](.+?)["']\s*\)""",
    re.IGNORECASE,
)


async def _run_dl_query(
    server_url: str,
    ontology_id: str,
    dl_query: str,
    query_type: str,
    timeout: int = 30,
) -> List[str]:
    """Run a DL query against an ontology server and return class IRIs."""
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
                    results = data.get("result", [])
                    return [item["class"] for item in results if "class" in item]
                else:
                    text = await resp.text()
                    logger.error(
                        "DL query to %s failed (%s): %s", api_url, resp.status, text[:200]
                    )
                    return []
    except Exception as e:
        logger.error("DL query error for %s: %s", ontology_id, e)
        return []


async def expand_sparql_query(
    sparql: str,
    server_lookup: Dict[str, str],
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Expand OWL DL query patterns in a SPARQL query.

    Args:
        sparql: The SPARQL query string with embedded OWL patterns.
        server_lookup: Dict mapping ontology_id -> server_url.

    Returns:
        Tuple of (expanded_sparql, expansions_info).
    """
    expanded = sparql
    expansions = []

    # Process VALUES patterns
    for match in VALUES_OWL_PATTERN.finditer(sparql):
        variable = match.group(1)
        query_type = match.group(2)
        ontology_id = match.group(3)
        dl_query = match.group(4).strip()

        server_url = server_lookup.get(ontology_id.lower())
        if not server_url:
            logger.warning("No server found for ontology %s", ontology_id)
            continue

        iris = await _run_dl_query(server_url, ontology_id, dl_query, query_type)
        iri_list = " ".join(f"<{iri}>" for iri in iris)
        replacement = f"VALUES {variable} {{ {iri_list} }}"
        expanded = expanded.replace(match.group(0), replacement)
        expansions.append({
            "pattern": "VALUES",
            "variable": variable,
            "ontology": ontology_id,
            "dl_query": dl_query,
            "type": query_type,
            "result_count": len(iris),
        })

    # Process FILTER patterns
    for match in FILTER_OWL_PATTERN.finditer(expanded):
        variable = match.group(1)
        query_type = match.group(2)
        ontology_id = match.group(3)
        dl_query = match.group(4).strip()

        server_url = server_lookup.get(ontology_id.lower())
        if not server_url:
            logger.warning("No server found for ontology %s", ontology_id)
            continue

        iris = await _run_dl_query(server_url, ontology_id, dl_query, query_type)
        iri_list = ", ".join(f"<{iri}>" for iri in iris)
        replacement = f"FILTER ({variable} IN ({iri_list}))"
        expanded = expanded.replace(match.group(0), replacement)
        expansions.append({
            "pattern": "FILTER",
            "variable": variable,
            "ontology": ontology_id,
            "dl_query": dl_query,
            "type": query_type,
            "result_count": len(iris),
        })

    return expanded, expansions


async def execute_sparql(
    sparql: str,
    endpoint: str,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Execute a SPARQL query against a SPARQL endpoint and return results."""
    params = {
        "query": sparql,
        "format": "application/sparql-results+json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                endpoint,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                else:
                    text = await resp.text()
                    return {"error": f"SPARQL endpoint returned {resp.status}: {text[:500]}"}
    except Exception as e:
        return {"error": f"SPARQL execution error: {str(e)}"}
