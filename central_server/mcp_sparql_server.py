#!/usr/bin/env python3
"""
AberOWL MCP SPARQL Expansion Server

Provides MCP tools for executing SPARQL queries enhanced with embedded
OWL Description Logic query expansion -- AberOWL's unique feature.

This allows agents to combine the expressiveness of OWL DL reasoning
with the power of SPARQL graph queries.

Tools:
  - expand_sparql: Execute SPARQL with embedded OWL DL query expansion
  - list_sparql_examples: Get example queries showing expansion patterns
  - explain_expansion: Explain what a DL-enhanced SPARQL query does

Usage:
  python mcp_sparql_server.py          # streamable HTTP on port 8767
  python mcp_sparql_server.py --stdio  # stdio transport (Claude Desktop)
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

server = Server("aberowl-sparql")


async def _api_post(path: str, body: dict) -> dict:
    """Make a POST request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=body, timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            text = await resp.text()
            return {"error": f"HTTP {resp.status}: {text[:500]}"}


async def _api_get(path: str, params: dict | None = None) -> dict:
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
            name="expand_sparql",
            description=(
                "Execute a SPARQL query with embedded OWL Description Logic "
                "query expansion. This is AberOWL's unique feature that combines "
                "OWL reasoning with SPARQL graph queries.\n\n"
                "## Expansion Patterns\n\n"
                "### VALUES pattern\n"
                "Embeds DL query results as a VALUES clause:\n"
                "```sparql\n"
                "SELECT ?class ?label WHERE {\n"
                "  VALUES ?class { OWL subeq GO { 'part of' some 'cell' } }\n"
                "  ?class rdfs:label ?label .\n"
                "}\n"
                "```\n"
                "This finds all classes that are 'part of some cell' in the GO "
                "ontology using OWL reasoning, then uses those IRIs to query "
                "the RDF graph for labels.\n\n"
                "### FILTER pattern\n"
                "Embeds DL query results as a FILTER IN clause:\n"
                "```sparql\n"
                "SELECT ?class ?label WHERE {\n"
                "  ?class rdfs:label ?label .\n"
                "  FILTER OWL(?class, subeq, GO, \"'part of' some 'cell'\")\n"
                "}\n"
                "```\n\n"
                "## Syntax\n"
                "- VALUES: `VALUES ?var { OWL type ontology_id { dl_query } }`\n"
                "- FILTER: `FILTER OWL(?var, type, ontology_id, \"dl_query\")`\n\n"
                "Where:\n"
                "- `?var` is the SPARQL variable to bind results to\n"
                "- `type` is: subclass, subeq, superclass, supeq, equivalent\n"
                "- `ontology_id` is the ontology to reason over (e.g., GO, HP, CHEBI)\n"
                "- `dl_query` is Manchester OWL Syntax (e.g., \"'part of' some 'cell'\")\n\n"
                "The query is expanded (DL patterns replaced with actual class IRIs) "
                "and then executed against the central Virtuoso SPARQL endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SPARQL query with optional OWL expansion patterns",
                    },
                    "endpoint": {
                        "type": "string",
                        "description": "Optional: custom SPARQL endpoint URL (defaults to central Virtuoso)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_sparql_examples",
            description=(
                "Get example SPARQL queries demonstrating OWL DL query expansion "
                "patterns. Useful for understanding how to combine SPARQL with "
                "OWL reasoning in AberOWL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ontology": {
                        "type": "string",
                        "description": "Optional: get examples specific to an ontology",
                    },
                },
            },
        ),
        Tool(
            name="explain_expansion",
            description=(
                "Explain what a DL-enhanced SPARQL query does step by step, "
                "showing how the OWL expansion patterns will be resolved. "
                "Does NOT execute the query -- just explains it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SPARQL query with OWL expansion patterns to explain",
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "expand_sparql":
            query = arguments["query"]
            body = {"query": query}
            if arguments.get("endpoint"):
                body["endpoint"] = arguments["endpoint"]

            data = await _api_post("/api/sparql", body)

            if "error" in data:
                return [TextContent(type="text", text=f"Error: {data['error']}")]

            lines = []

            # Show expansion info if any patterns were expanded
            expansions = data.get("expansions")
            if expansions:
                lines.append("## OWL Expansions Applied\n")
                for exp in expansions:
                    lines.append(
                        f"  - {exp['pattern']} {exp['variable']}: "
                        f"{exp['type']} query on {exp['ontology']} "
                        f"-> {exp['result_count']} classes"
                    )
                lines.append("")

            # Show results
            results = data.get("results", {})
            if "error" in results:
                lines.append(f"SPARQL execution error: {results['error']}")
            else:
                bindings = results.get("results", {}).get("bindings", [])
                if not bindings:
                    lines.append("Query returned no results.")
                else:
                    # Format as table
                    vars_list = results.get("head", {}).get("vars", [])
                    lines.append(f"## Results ({len(bindings)} rows)\n")
                    if vars_list:
                        lines.append("| " + " | ".join(vars_list) + " |")
                        lines.append("| " + " | ".join("---" for _ in vars_list) + " |")
                    for row in bindings[:100]:
                        values = []
                        for v in vars_list:
                            val = row.get(v, {}).get("value", "")
                            values.append(val)
                        lines.append("| " + " | ".join(values) + " |")
                    if len(bindings) > 100:
                        lines.append(f"\n... and {len(bindings) - 100} more rows")

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "list_sparql_examples":
            ontology = arguments.get("ontology", "GO")
            examples = [
                {
                    "title": "Find all subclasses of a concept and their labels",
                    "query": (
                        f"SELECT ?class ?label WHERE {{\n"
                        f"  VALUES ?class {{ OWL subeq {ontology} {{ 'cell' }} }}\n"
                        f"  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
                        f"}}"
                    ),
                },
                {
                    "title": "Find classes related via an object property",
                    "query": (
                        f"SELECT ?class ?label WHERE {{\n"
                        f"  VALUES ?class {{ OWL subeq {ontology} {{ 'part of' some 'cell' }} }}\n"
                        f"  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
                        f"}}"
                    ),
                },
                {
                    "title": "FILTER pattern: combine graph patterns with DL reasoning",
                    "query": (
                        f"SELECT ?class ?label ?definition WHERE {{\n"
                        f"  ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
                        f"  OPTIONAL {{ ?class <http://purl.obolibrary.org/obo/IAO_0000115> ?definition }}\n"
                        f"  FILTER OWL(?class, subeq, {ontology}, \"'cell'\")\n"
                        f"}}"
                    ),
                },
                {
                    "title": "Cross-ontology: find GO processes and their HP phenotypes",
                    "query": (
                        "SELECT ?go_class ?go_label ?hp_class ?hp_label WHERE {\n"
                        "  VALUES ?go_class { OWL subeq GO { 'apoptotic process' } }\n"
                        "  ?go_class <http://www.w3.org/2000/01/rdf-schema#label> ?go_label .\n"
                        "  ?hp_class <http://purl.obolibrary.org/obo/RO_0002200> ?go_class .\n"
                        "  ?hp_class <http://www.w3.org/2000/01/rdf-schema#label> ?hp_label .\n"
                        "}"
                    ),
                },
            ]

            lines = [f"## SPARQL + OWL Expansion Examples (using {ontology})\n"]
            for i, ex in enumerate(examples, 1):
                lines.append(f"### {i}. {ex['title']}\n")
                lines.append(f"```sparql\n{ex['query']}\n```\n")

            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "explain_expansion":
            import re
            query = arguments["query"]
            lines = ["## Query Expansion Explanation\n"]
            lines.append("### Original Query\n")
            lines.append(f"```sparql\n{query}\n```\n")

            # Find VALUES patterns
            values_pattern = r"VALUES\s+(\?\w+)\s*\{\s*OWL\s+(\w+)\s+(\w+)\s*\{\s*(.*?)\s*\}\s*\}"
            for match in re.finditer(values_pattern, query, re.IGNORECASE | re.DOTALL):
                var, qtype, ont, dl = match.group(1), match.group(2), match.group(3), match.group(4)
                lines.append(f"### VALUES Expansion: {var}")
                lines.append(f"  1. AberOWL will run a **{qtype}** DL query on **{ont}**")
                lines.append(f"  2. DL query: `{dl.strip()}`")
                lines.append(f"  3. The OWL reasoner classifies {ont} and finds matching classes")
                lines.append(f"  4. The result IRIs replace the OWL pattern as VALUES bindings")
                lines.append(f"  5. The SPARQL endpoint sees only standard SPARQL with VALUES\n")

            # Find FILTER patterns
            filter_pattern = r"""FILTER\s+OWL\(\s*(\?\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*["'](.+?)["']\s*\)"""
            for match in re.finditer(filter_pattern, query, re.IGNORECASE):
                var, qtype, ont, dl = match.group(1), match.group(2), match.group(3), match.group(4)
                lines.append(f"### FILTER Expansion: {var}")
                lines.append(f"  1. AberOWL will run a **{qtype}** DL query on **{ont}**")
                lines.append(f"  2. DL query: `{dl.strip()}`")
                lines.append(f"  3. The result IRIs are placed into a FILTER ... IN (...) clause")
                lines.append(f"  4. This restricts {var} to only classes matching the DL query\n")

            if len(lines) == 3:
                lines.append("No OWL expansion patterns found in this query.")
                lines.append("This is a plain SPARQL query that will be executed as-is.")

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
        port = int(os.getenv("MCP_SPARQL_PORT", "8767"))
        http_server = StreamableHTTPServer(server, host="0.0.0.0", port=port)
        print(f"AberOWL SPARQL MCP server running on http://0.0.0.0:{port}/mcp")
        await http_server.run()


if __name__ == "__main__":
    asyncio.run(main())
