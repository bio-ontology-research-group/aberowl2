#!/usr/bin/env python3
"""
AberOWL MCP Ontology Server

Provides MCP tools for querying and browsing ontologies in the AberOWL
repository. Uses streamable HTTP transport for both local and remote access.

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

import asyncio
import json
import os
import sys
from typing import Any

import aiohttp
from mcp.server import Server
from mcp.types import TextContent, Tool

CENTRAL_SERVER_URL = os.getenv("CENTRAL_SERVER_URL", "http://localhost:80")

server = Server("aberowl-ontology")


async def _api_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            return {"error": f"HTTP {resp.status}: {text[:500]}"}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_ontologies",
            description=(
                "List all ontologies in the AberOWL repository with their status "
                "(online/offline), class count, and other metadata. Use this to "
                "discover available ontologies before querying them."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="search_classes",
            description=(
                "Search for ontology classes by label, synonym, or OBO ID across "
                "all ontologies (or a specific one). Returns matching classes with "
                "their IRIs, labels, definitions, and source ontology.\n\n"
                "Examples:\n"
                "  - Search for 'apoptosis' across all ontologies\n"
                "  - Search for 'GO:0006915' by OBO ID\n"
                "  - Search for 'cell death' in the GO ontology specifically"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term (label, synonym, or OBO ID)",
                    },
                    "ontology": {
                        "type": "string",
                        "description": "Optional: restrict search to a specific ontology ID (e.g., 'go', 'hp')",
                    },
                    "size": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="run_dl_query",
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
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "DL query in Manchester OWL Syntax",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["subclass", "subeq", "superclass", "supeq", "equivalent"],
                        "description": "Query type (default: subeq)",
                        "default": "subeq",
                    },
                    "ontology": {
                        "type": "string",
                        "description": "Optional: restrict to a specific ontology ID",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_class_info",
            description=(
                "Get detailed information about a specific ontology class, "
                "including all annotations (labels, definitions, synonyms), "
                "axioms, and relationships. Requires both the class IRI and "
                "the ontology ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "class_iri": {
                        "type": "string",
                        "description": "The full IRI of the class (e.g., 'http://purl.obolibrary.org/obo/GO_0005623')",
                    },
                    "ontology": {
                        "type": "string",
                        "description": "The ontology ID (e.g., 'go', 'hp')",
                    },
                },
                "required": ["class_iri", "ontology"],
            },
        ),
        Tool(
            name="get_ontology_info",
            description=(
                "Get metadata about a specific ontology including title, "
                "description, version, class count, property count, license, "
                "and classification status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ontology": {
                        "type": "string",
                        "description": "The ontology ID (e.g., 'go', 'hp', 'chebi')",
                    },
                },
                "required": ["ontology"],
            },
        ),
        Tool(
            name="browse_hierarchy",
            description=(
                "Browse the class hierarchy of an ontology. Get the direct "
                "subclasses or superclasses of a given class. Use this to "
                "navigate the ontology tree structure.\n\n"
                "Pass a class IRI (or 'owl:Thing' for the root) and direction "
                "(subclass or superclass) to explore the hierarchy."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "class_iri": {
                        "type": "string",
                        "description": "Class IRI to browse from (use 'owl:Thing' or '<http://www.w3.org/2002/07/owl#Thing>' for root)",
                    },
                    "ontology": {
                        "type": "string",
                        "description": "The ontology ID",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["subclass", "superclass"],
                        "description": "Direction to browse (default: subclass)",
                        "default": "subclass",
                    },
                },
                "required": ["class_iri", "ontology"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "list_ontologies":
            data = await _api_get("/api/listOntologies")
            ontologies = data.get("result", [])
            lines = [f"Found {len(ontologies)} ontologies:\n"]
            for ont in ontologies:
                status = "ONLINE" if ont.get("status") == "online" else "offline"
                lines.append(f"  [{status}] {ont.get('id', '?')} - {ont.get('title', '')}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "search_classes":
            query = arguments["query"]
            params = {"query": query, "size": str(arguments.get("size", 50))}
            if arguments.get("ontology"):
                params["ontologies"] = arguments["ontology"]
            data = await _api_get("/api/search_all", params)
            results = data.get("result", [])
            if not results:
                return [TextContent(type="text", text=f"No results found for '{query}'")]
            lines = [f"Found {len(results)} results for '{query}':\n"]
            for r in results[:50]:
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
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "run_dl_query":
            query = arguments["query"]
            qtype = arguments.get("type", "subeq")
            params = {"query": query, "type": qtype, "labels": "true"}
            if arguments.get("ontology"):
                params["ontologies"] = arguments["ontology"]
            data = await _api_get("/api/dlquery_all", params)
            results = data.get("result", [])
            if not results:
                return [TextContent(type="text", text=f"No results for DL query: {query} (type: {qtype})")]
            lines = [f"Found {len(results)} results for {qtype} query: {query}\n"]
            for r in results[:100]:
                label = r.get("label", r.get("owlClass", "?"))
                ont = r.get("ontology", "?")
                iri = r.get("class", "")
                lines.append(f"  {label} [{ont}] - {iri}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "get_class_info":
            data = await _api_get("/api/getClass", {
                "query": arguments["class_iri"],
                "ontology": arguments["ontology"],
            })
            if "error" in data:
                return [TextContent(type="text", text=f"Error: {data['error']}")]
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "get_ontology_info":
            data = await _api_get("/api/getOntology", {"ontology": arguments["ontology"]})
            if "error" in data or "detail" in data:
                return [TextContent(type="text", text=f"Error: {data.get('error') or data.get('detail')}")]
            return [TextContent(type="text", text=json.dumps(data, indent=2))]

        elif name == "browse_hierarchy":
            class_iri = arguments["class_iri"]
            if class_iri == "owl:Thing":
                class_iri = "<http://www.w3.org/2002/07/owl#Thing>"
            direction = arguments.get("direction", "subclass")
            params = {
                "query": class_iri,
                "type": direction,
                "ontologies": arguments["ontology"],
                "labels": "true",
            }
            data = await _api_get("/api/dlquery_all", params)
            results = data.get("result", [])
            direction_label = "subclasses" if direction == "subclass" else "superclasses"
            if not results:
                return [TextContent(type="text", text=f"No {direction_label} found for {class_iri}")]
            lines = [f"Direct {direction_label} of {class_iri} ({len(results)} found):\n"]
            for r in results:
                label = r.get("label", r.get("owlClass", "?"))
                iri = r.get("class", "")
                lines.append(f"  {label} - {iri}")
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    if "--stdio" in sys.argv:
        from mcp.server.stdio import stdio_server
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    else:
        from mcp.server.streamable_http import StreamableHTTPServer
        port = int(os.getenv("MCP_ONTOLOGY_PORT", "8766"))
        http_server = StreamableHTTPServer(server, host="0.0.0.0", port=port)
        print(f"AberOWL Ontology MCP server running on http://0.0.0.0:{port}/mcp")
        await http_server.run()


if __name__ == "__main__":
    asyncio.run(main())
