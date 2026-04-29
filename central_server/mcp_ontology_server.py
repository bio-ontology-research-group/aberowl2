#!/usr/bin/env python3
"""
AberOWL MCP Ontology Server

Provides MCP tools for querying and browsing ontologies in the AberOWL
repository. Uses FastMCP with streamable HTTP transport for both local
and remote access.

Tools:
  - list_ontologies: List all registered ontologies with status
  - search_classes: Search for classes by label/synonym across ontologies
  - run_dl_query: Execute a Description Logic query in Manchester OWL Syntax
  - get_class_info: Get detailed annotations for a specific class
  - get_ontology_info: Get metadata about a specific ontology
  - browse_hierarchy: Get subclasses/superclasses of a class

Usage:
  python mcp_ontology_server.py          # streamable HTTP on port 8766
  python mcp_ontology_server.py --stdio  # stdio transport (Claude Desktop)
"""

import json
import os
import sys
from typing import Any

import aiohttp
from mcp.server.fastmcp import FastMCP

CENTRAL_SERVER_URL = os.getenv("CENTRAL_SERVER_URL", "http://localhost:80")
PORT = int(os.getenv("MCP_ONTOLOGY_PORT", "8766"))

mcp = FastMCP(
    "aberowl-ontology",
    host="0.0.0.0",
    port=PORT,
)


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            return {"error": f"HTTP {resp.status}: {text[:500]}"}


@mcp.tool(
    description=(
        "List all ontologies in the AberOWL repository with their status "
        "(online/offline), class count, and other metadata. Use this to "
        "discover available ontologies before querying them."
    ),
)
async def list_ontologies() -> str:
    data = await _api_get("/api/listOntologies")
    ontologies = data.get("result", [])
    lines = [f"Found {len(ontologies)} ontologies:\n"]
    for ont in ontologies:
        status = "ONLINE" if ont.get("status") == "online" else "offline"
        lines.append(f"  [{status}] {ont.get('id', '?')} - {ont.get('title', '')}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Search for ontology classes by label, synonym, or OBO ID across "
        "all ontologies (or a specific one). Returns matching classes with "
        "their IRIs, labels, definitions, and source ontology.\n\n"
        "Examples:\n"
        "  - Search for 'apoptosis' across all ontologies\n"
        "  - Search for 'GO:0006915' by OBO ID\n"
        "  - Search for 'cell death' in the GO ontology specifically"
    ),
)
async def search_classes(query: str, ontology: str | None = None, size: int = 50) -> str:
    """
    Args:
        query: Search term (label, synonym, or OBO ID)
        ontology: Optional ontology ID to restrict the search (e.g. 'go', 'hp')
        size: Maximum number of results (default 50)
    """
    params: dict[str, Any] = {"query": query, "size": str(size)}
    if ontology:
        params["ontologies"] = ontology
    data = await _api_get("/api/search_all", params)
    results = data.get("result", [])
    if not results:
        return f"No results found for '{query}'"
    lines = [f"Found {len(results)} results for '{query}':\n"]
    for r in results[:size]:
        label = r.get("label", r.get("class", "?"))
        if isinstance(label, list):
            label = label[0] if label else "?"
        ont = r.get("ontology", "?")
        iri = r.get("class", "")
        defn = r.get("definition", "")
        if isinstance(defn, list):
            defn = defn[0] if defn else ""
        lines.append(f"  {label} [{ont}]")
        lines.append(f"    IRI: {iri}")
        if defn:
            lines.append(f"    Def: {defn[:150]}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Run a Description Logic (DL) query in Manchester OWL Syntax "
        "against one or all ontologies. This uses OWL reasoning to find "
        "classes based on logical relationships.\n\n"
        "Manchester OWL Syntax guide:\n"
        "  - Named classes: use the label in single quotes, e.g., 'cell'\n"
        "  - Class IRIs: use angle brackets, e.g., <http://purl.obolibrary.org/obo/GO_0005623>\n"
        "  - Intersection: 'cell' and 'part of' some 'organism'\n"
        "  - Union: 'cell' or 'tissue'\n"
        "  - Existential: 'part of' some 'cell'\n"
        "  - Universal: 'part of' only 'cell'\n"
        "  - Negation: not 'cell'\n\n"
        "Query types:\n"
        "  - subclass: direct subclasses only\n"
        "  - subeq: subclasses + equivalent (most common)\n"
        "  - superclass: direct superclasses only\n"
        "  - supeq: superclasses + equivalent\n"
        "  - equivalent: equivalent classes only\n\n"
        "Examples:\n"
        "  - query='cell', type='subclass' -> all subclasses of 'cell'\n"
        "  - query=\"'part of' some 'cell'\", type='subeq' -> classes that are part of a cell\n"
        "  - query=\"'has part' some 'nucleus'\", type='subeq' -> things that have a nucleus"
    ),
)
async def run_dl_query(query: str, type: str = "subeq", ontology: str | None = None) -> str:
    """
    Args:
        query: DL query in Manchester OWL Syntax
        type: Query type - subclass, subeq, superclass, supeq, or equivalent (default subeq)
        ontology: Optional ontology ID to restrict reasoning
    """
    params: dict[str, Any] = {"query": query, "type": type, "labels": "true"}
    if ontology:
        params["ontologies"] = ontology
    data = await _api_get("/api/dlquery_all", params)
    results = data.get("result", [])
    if not results:
        return f"No results for DL query: {query} (type: {type})"
    lines = [f"Found {len(results)} results for {type} query: {query}\n"]
    for r in results[:100]:
        label = r.get("label", r.get("owlClass", "?"))
        ont = r.get("ontology", "?")
        iri = r.get("class", "")
        lines.append(f"  {label} [{ont}] - {iri}")
    return "\n".join(lines)


@mcp.tool(
    description=(
        "Get detailed information about a specific ontology class, "
        "including all annotations (labels, definitions, synonyms), "
        "axioms, and relationships. Requires both the class IRI and "
        "the ontology ID."
    ),
)
async def get_class_info(class_iri: str, ontology: str) -> str:
    """
    Args:
        class_iri: Full IRI of the class (e.g. 'http://purl.obolibrary.org/obo/GO_0005623')
        ontology: Ontology ID (e.g. 'go', 'hp')
    """
    data = await _api_get("/api/getClass", {"query": class_iri, "ontology": ontology})
    if "error" in data:
        return f"Error: {data['error']}"
    return json.dumps(data, indent=2)


@mcp.tool(
    description=(
        "Get metadata about a specific ontology including title, "
        "description, version, class count, property count, license, "
        "and classification status."
    ),
)
async def get_ontology_info(ontology: str) -> str:
    """
    Args:
        ontology: Ontology ID (e.g. 'go', 'hp', 'chebi')
    """
    data = await _api_get("/api/getOntology", {"ontology": ontology})
    if "error" in data or "detail" in data:
        return f"Error: {data.get('error') or data.get('detail')}"
    return json.dumps(data, indent=2)


@mcp.tool(
    description=(
        "Browse the class hierarchy of an ontology. Get the direct "
        "subclasses or superclasses of a given class. Use this to "
        "navigate the ontology tree structure.\n\n"
        "Pass a class IRI (or 'owl:Thing' for the root) and direction "
        "(subclass or superclass) to explore the hierarchy."
    ),
)
async def browse_hierarchy(class_iri: str, ontology: str, direction: str = "subclass") -> str:
    """
    Args:
        class_iri: Class IRI to browse from (use 'owl:Thing' for root)
        ontology: Ontology ID
        direction: 'subclass' or 'superclass' (default subclass)
    """
    if class_iri == "owl:Thing":
        class_iri = "<http://www.w3.org/2002/07/owl#Thing>"
    params = {
        "query": class_iri,
        "type": direction,
        "ontologies": ontology,
        "labels": "true",
    }
    data = await _api_get("/api/dlquery_all", params)
    results = data.get("result", [])
    label = "subclasses" if direction == "subclass" else "superclasses"
    if not results:
        return f"No {label} found for {class_iri}"
    lines = [f"Direct {label} of {class_iri} ({len(results)} found):\n"]
    for r in results:
        lbl = r.get("label", r.get("owlClass", "?"))
        iri = r.get("class", "")
        lines.append(f"  {lbl} - {iri}")
    return "\n".join(lines)


def main() -> None:
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print(f"AberOWL Ontology MCP server running on http://0.0.0.0:{PORT}/mcp")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
