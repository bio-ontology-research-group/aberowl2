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
  - rewrite_sparql: Rewrite SPARQL with embedded OWL DL frames into plain SPARQL
  - list_sparql_examples: Curated SPARQL+OWL example queries to use as templates
  - query_sparql: Rewrite + execute against an external endpoint (Ontobee by default)

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


async def _api_post(path: str, body: dict) -> dict:
    """Make a POST request to the central AberOWL API."""
    url = f"{CENTRAL_SERVER_URL.rstrip('/')}{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=60)) as resp:
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


@mcp.tool(
    description=(
        "Rewrite a SPARQL query that contains embedded OWL DL frames into "
        "plain SPARQL with concrete class IRIs spliced in. AberOWL only "
        "rewrites — it does not execute. Run the returned query against "
        "any SPARQL endpoint you choose (Ontobee, UniProt, Wikidata, …).\n\n"
        "Two embedded frame patterns are supported:\n"
        "  1. VALUES ?var { OWL <type> <ontology_id> { dl_query } }\n"
        "  2. FILTER OWL(?var, <type>, <ontology_id>, \"dl_query\")\n\n"
        "Where <type> is subclass | superclass | equivalent | subeq | supeq, "
        "<ontology_id> is a registered AberOWL ontology id (case-insensitive; "
        "may contain '-' or '.', e.g. go-plus, chebi.ext), and <dl_query> is "
        "a Manchester OWL Syntax expression — a class label like 'cell death' "
        "or a compound such as 'part of' some 'cell'.\n\n"
        "Examples (call list_sparql_examples for the full set):\n"
        "  • Subclasses of 'cell death' in go-plus (VALUES form):\n"
        "      SELECT ?c ?label WHERE {\n"
        "        VALUES ?c { OWL subeq go-plus { 'cell death' } }\n"
        "        ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
        "      }\n\n"
        "  • UniProt federation — proteins classified under GO subclasses:\n"
        "      SELECT ?protein ?goClass WHERE {\n"
        "        VALUES ?goClass { OWL subeq go-plus { 'cell death' } }\n"
        "        ?protein a <http://purl.uniprot.org/core/Protein> ;\n"
        "                 <http://purl.uniprot.org/core/classifiedWith> ?goClass .\n"
        "      } LIMIT 50\n\n"
        "  • FILTER form, equivalent semantics:\n"
        "      SELECT ?c WHERE {\n"
        "        ?c <http://www.w3.org/2000/01/rdf-schema#label> ?l .\n"
        "        FILTER OWL(?c, subeq, go-plus, \"'part of' some 'cell'\")\n"
        "      }\n\n"
        "Frames whose ontology is unknown or whose worker is offline are "
        "reported as errors and replaced with an empty match in the rewritten "
        "query, so the rest of the query is still usable."
    ),
)
async def rewrite_sparql(query: str) -> str:
    """
    Args:
        query: SPARQL query string containing one or more OWL DL frames.
    """
    data = await _api_post("/api/sparql", {"query": query})
    if data.get("error") and "rewritten_query" not in data:
        return f"Error: {data['error']}"

    rewritten = data.get("rewritten_query", "")
    expansions = data.get("expansions") or []
    errors = data.get("errors") or []

    lines: list[str] = []
    if expansions:
        lines.append(f"Resolved {len(expansions)} OWL frame(s):")
        for exp in expansions:
            lines.append(
                f"  - {exp.get('pattern')} {exp.get('variable')}: "
                f"{exp.get('type')} on {exp.get('ontology')} → "
                f"{exp.get('result_count')} classes"
            )
        lines.append("")
    if errors:
        lines.append(f"Could not resolve {len(errors)} frame(s) "
                     "(replaced with an empty match in the rewritten query):")
        for err in errors:
            lines.append(
                f"  - {err.get('pattern')} {err.get('variable')} "
                f"({err.get('type')} on {err.get('ontology')}): {err.get('error')}"
            )
        lines.append("")
    if not expansions and not errors:
        lines.append("No OWL DL frames found in the query — returned unchanged.\n")

    lines.append("Rewritten query:")
    lines.append(rewritten or "(empty)")
    return "\n".join(lines)


_SPARQL_EXAMPLES: list[dict[str, str]] = [
    {
        "title": "Subclasses of 'cell death' in GO (VALUES form)",
        "ontology": "go-plus",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Resolves every subclass of 'cell death' in GO via the reasoner, "
            "then asks Ontobee for each class's label."
        ),
        "query": (
            "SELECT ?c ?label WHERE {\n"
            "  VALUES ?c { OWL subeq go-plus { 'cell death' } }\n"
            "  ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "UniProt: proteins classified under a GO subtree",
        "ontology": "go-plus",
        "endpoint": "https://sparql.uniprot.org/sparql",
        "description": (
            "Federation pattern — AberOWL resolves the GO subtree, the "
            "rewritten query goes to UniProt to find matching proteins."
        ),
        "query": (
            "PREFIX up: <http://purl.uniprot.org/core/>\n"
            "SELECT ?protein ?mnemonic ?goClass WHERE {\n"
            "  VALUES ?goClass { OWL subeq go-plus { 'cell death' } }\n"
            "  ?protein a up:Protein ;\n"
            "           up:classifiedWith ?goClass ;\n"
            "           up:mnemonic ?mnemonic .\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "Manchester compound DL query (FILTER form)",
        "ontology": "go-plus",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Compound DL queries work the same way: any Manchester syntax "
            "is accepted inside the OWL frame."
        ),
        "query": (
            "SELECT ?c ?label WHERE {\n"
            "  ?c <http://www.w3.org/2000/01/rdf-schema#label> ?label .\n"
            "  FILTER OWL(?c, subeq, go-plus, \"'part of' some 'cell'\")\n"
            "} LIMIT 50"
        ),
    },
    {
        "title": "Multiple frames in one query",
        "ontology": "go-plus, chebi",
        "endpoint": "https://sparql.hegroup.org/sparql",
        "description": (
            "Each OWL frame is resolved independently against the named "
            "ontology — mix and match across ontologies in one rewrite."
        ),
        "query": (
            "SELECT ?go ?chem WHERE {\n"
            "  VALUES ?go   { OWL subeq go-plus { 'apoptotic process' } }\n"
            "  VALUES ?chem { OWL subeq chebi   { 'small molecule' } }\n"
            "}"
        ),
    },
    {
        "title": "Pizza demo — works against the local test worker",
        "ontology": "pizza",
        "endpoint": "(use the /api/sparql rewriter only — no public endpoint)",
        "description": (
            "Smallest reproducible example, useful for verifying the local "
            "test harness before pointing at GO / UniProt."
        ),
        "query": (
            "SELECT ?c WHERE {\n"
            "  VALUES ?c { OWL subeq pizza { Pizza } }\n"
            "}"
        ),
    },
]


@mcp.tool(
    description=(
        "Return curated SPARQL example queries that demonstrate the OWL DL "
        "frame syntax accepted by `rewrite_sparql`. Each example includes a "
        "title, target ontology, suggested SPARQL endpoint to run the "
        "rewritten query against, and the query itself. Use these as "
        "templates when building new queries."
    ),
)
async def list_sparql_examples() -> str:
    lines: list[str] = [f"{len(_SPARQL_EXAMPLES)} SPARQL + OWL examples:\n"]
    for i, ex in enumerate(_SPARQL_EXAMPLES, 1):
        lines.append(f"--- {i}. {ex['title']} ---")
        lines.append(f"Ontology: {ex['ontology']}")
        lines.append(f"Endpoint: {ex['endpoint']}")
        lines.append(f"What it does: {ex['description']}")
        lines.append("Query:")
        for q_line in ex["query"].splitlines():
            lines.append(f"  {q_line}")
        lines.append("")
    lines.append(
        "To resolve any of these, pass the query to `rewrite_sparql`. "
        "AberOWL returns the rewritten SPARQL with concrete IRIs spliced "
        "in; you then run it against the suggested endpoint."
    )
    return "\n".join(lines)


DEFAULT_SPARQL_ENDPOINT = "https://sparql.hegroup.org/sparql"  # Ontobee


async def _execute_sparql(endpoint: str, query: str, timeout: int = 60) -> dict[str, Any]:
    """POST a SPARQL query to an external endpoint and return the JSON results.

    AberOWL itself does not host a SPARQL store; this helper just forwards
    to whatever endpoint the user chose.
    """
    headers = {
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                data={"query": query},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    return {"error": f"endpoint returned HTTP {resp.status}: {text[:500]}"}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"error": f"endpoint returned non-JSON: {text[:500]}"}
    except Exception as e:
        return {"error": f"endpoint unreachable: {e}"}


def _format_sparql_results(results: dict[str, Any], limit: int = 50) -> list[str]:
    """Render a SPARQL JSON results object as compact text rows."""
    head = results.get("head", {})
    body = results.get("results", {})
    bindings = body.get("bindings", []) if isinstance(body, dict) else []
    vars_ = head.get("vars", []) if isinstance(head, dict) else []

    if not bindings:
        # ASK queries put the answer in `boolean`.
        if "boolean" in results:
            return [f"ASK result: {results['boolean']}"]
        return ["No results."]

    lines: list[str] = [f"{len(bindings)} row(s)" + (f" (showing first {limit})" if len(bindings) > limit else "") + ":"]
    for row in bindings[:limit]:
        cells = []
        for v in vars_:
            cell = row.get(v, {})
            value = cell.get("value", "") if isinstance(cell, dict) else ""
            cells.append(f"{v}={value}")
        lines.append("  " + " | ".join(cells))
    return lines


@mcp.tool(
    description=(
        "Rewrite a SPARQL query containing OWL DL frames AND execute it "
        "against an external SPARQL endpoint, returning the result rows. "
        "AberOWL still only does the rewriting; this tool just forwards "
        "the rewritten SPARQL to the chosen endpoint and formats whatever "
        "comes back.\n\n"
        "If `endpoint` is omitted, the rewritten query runs against "
        f"Ontobee ({DEFAULT_SPARQL_ENDPOINT}). Common alternatives:\n"
        "  - https://sparql.uniprot.org/sparql       (UniProt)\n"
        "  - https://query.wikidata.org/sparql       (Wikidata)\n"
        "  - https://dbpedia.org/sparql              (DBpedia)\n"
        "  - https://bio2rdf.org/sparql              (Bio2RDF)\n\n"
        "Frame syntax is identical to `rewrite_sparql`. Per-frame "
        "resolution failures (unknown ontology, offline worker, DL parse "
        "error) are reported but the rest of the query is still executed.\n\n"
        "Example: SELECT ?c ?label WHERE { VALUES ?c { OWL subeq go-plus { 'cell death' } } "
        "?c <http://www.w3.org/2000/01/rdf-schema#label> ?label . } LIMIT 50"
    ),
)
async def query_sparql(query: str, endpoint: str | None = None) -> str:
    """
    Args:
        query:    SPARQL query string. May contain OWL DL frames; they will
                  be resolved by AberOWL before the query is executed.
        endpoint: SPARQL endpoint to execute against. Defaults to Ontobee
                  (https://sparql.hegroup.org/sparql).
    """
    target = (endpoint or DEFAULT_SPARQL_ENDPOINT).strip()

    rewrite = await _api_post("/api/sparql", {"query": query})
    if rewrite.get("error") and "rewritten_query" not in rewrite:
        return f"Rewrite error: {rewrite['error']}"

    rewritten = rewrite.get("rewritten_query", "") or query
    expansions = rewrite.get("expansions") or []
    errors = rewrite.get("errors") or []

    lines: list[str] = [f"Endpoint: {target}"]
    if expansions:
        lines.append(f"Resolved {len(expansions)} OWL frame(s):")
        for exp in expansions:
            lines.append(
                f"  - {exp.get('pattern')} {exp.get('variable')}: "
                f"{exp.get('type')} on {exp.get('ontology')} → "
                f"{exp.get('result_count')} classes"
            )
    if errors:
        lines.append(f"Could not resolve {len(errors)} frame(s) "
                     "(replaced with an empty match in the executed query):")
        for err in errors:
            lines.append(
                f"  - {err.get('pattern')} {err.get('variable')} "
                f"({err.get('type')} on {err.get('ontology')}): {err.get('error')}"
            )

    results = await _execute_sparql(target, rewritten)
    if results.get("error"):
        lines.append("")
        lines.append(f"Endpoint error: {results['error']}")
        lines.append("")
        lines.append("Rewritten query (you can re-run this manually):")
        lines.append(rewritten)
        return "\n".join(lines)

    lines.append("")
    lines.extend(_format_sparql_results(results))
    return "\n".join(lines)


def main() -> None:
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        print(f"AberOWL Ontology MCP server running on http://0.0.0.0:{PORT}/mcp")
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
