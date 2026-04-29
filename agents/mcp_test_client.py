#!/usr/bin/env python3
"""
AberOWL MCP Test Client

Connects to the two AberOWL MCP servers over streamable-HTTP, lists every
advertised tool, then exercises each of the 9 tools with realistic
arguments. Prints pass/fail + per-call latency.

Usage:
    python agents/mcp_test_client.py
    python agents/mcp_test_client.py --ontology http://host:8766 --sparql http://host:8767

Env vars (used as defaults):
    MCP_ONTOLOGY_URL (default http://localhost:8766)
    MCP_SPARQL_URL   (default http://localhost:8767)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class CallOutcome:
    tool: str
    ok: bool
    elapsed: float
    note: str = ""


@dataclass
class ServerOutcome:
    name: str
    url: str
    tools_expected: set[str]
    tools_advertised: set[str] = field(default_factory=set)
    calls: list[CallOutcome] = field(default_factory=list)
    connect_error: str | None = None


ONTOLOGY_TOOLS = {
    "list_ontologies",
    "search_classes",
    "run_dl_query",
    "get_class_info",
    "get_ontology_info",
    "browse_hierarchy",
}

SPARQL_TOOLS = {
    "expand_sparql",
    "list_sparql_examples",
    "explain_expansion",
}


# Substrings that the MCP servers return when a call succeeds at the
# transport level but produces no results. Treat as failure for fixtures
# that are expected to hit, so we don't paper over silent regressions
# (the original symptom: SPARQL expander lowercase bug returned 0 IRIs
# but the test still passed).
_EMPTY_MARKERS = (
    "No results found",
    "No results for DL query",
    "No subclasses found",
    "No superclasses found",
    "No equivalent",
    "No examples",
    "Error:",
)


def _looks_empty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped in ("[]", "{}", "null"):
        return True
    return any(stripped.startswith(m) for m in _EMPTY_MARKERS)


async def _call(
    session: ClientSession,
    tool: str,
    args: dict[str, Any],
    expect_nonempty: bool = True,
) -> CallOutcome:
    start = time.perf_counter()
    try:
        result = await session.call_tool(tool, args)
        elapsed = time.perf_counter() - start
        # CallToolResult has .content (list) and .isError
        is_error = getattr(result, "isError", False)
        content = getattr(result, "content", result)
        first_text = ""
        if content:
            first = content[0]
            first_text = getattr(first, "text", str(first))
        note = first_text[:120].replace("\n", " ")
        ok = not is_error
        if ok and expect_nonempty and _looks_empty(first_text):
            ok = False
            note = f"empty result: {note}"
        return CallOutcome(tool, ok, elapsed, note)
    except Exception as e:
        elapsed = time.perf_counter() - start
        return CallOutcome(tool, False, elapsed, f"exception: {e}")


async def exercise_ontology_server(url: str) -> ServerOutcome:
    outcome = ServerOutcome("ontology", url, ONTOLOGY_TOOLS)
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                outcome.tools_advertised = {t.name for t in tools.tools}

                outcome.calls.append(await _call(session, "list_ontologies", {}))
                outcome.calls.append(await _call(
                    session, "search_classes", {"query": "apoptosis", "size": 5}
                ))
                outcome.calls.append(await _call(
                    session, "run_dl_query",
                    {"query": "'cell'", "type": "subeq", "ontology": "go"},
                ))
                outcome.calls.append(await _call(
                    session, "get_ontology_info", {"ontology": "go"},
                ))
                outcome.calls.append(await _call(
                    session, "get_class_info",
                    {
                        "class_iri": "http://purl.obolibrary.org/obo/GO_0005623",
                        "ontology": "go",
                    },
                ))
                outcome.calls.append(await _call(
                    session, "browse_hierarchy",
                    {"class_iri": "owl:Thing", "ontology": "go", "direction": "subclass"},
                ))
    except Exception as e:
        outcome.connect_error = f"{type(e).__name__}: {e}"
    return outcome


async def exercise_sparql_server(url: str) -> ServerOutcome:
    outcome = ServerOutcome("sparql", url, SPARQL_TOOLS)
    sample_query = (
        "SELECT ?c ?l WHERE {\n"
        "  VALUES ?c { OWL subeq GO { 'cell' } }\n"
        "  ?c <http://www.w3.org/2000/01/rdf-schema#label> ?l .\n"
        "} LIMIT 5"
    )
    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                outcome.tools_advertised = {t.name for t in tools.tools}

                outcome.calls.append(await _call(
                    session, "list_sparql_examples", {"ontology": "GO"}
                ))
                outcome.calls.append(await _call(
                    session, "explain_expansion", {"query": sample_query}
                ))
                outcome.calls.append(await _call(
                    session, "expand_sparql", {"query": sample_query}
                ))
    except Exception as e:
        outcome.connect_error = f"{type(e).__name__}: {e}"
    return outcome


def _print_outcome(o: ServerOutcome) -> int:
    """Print report for one server, return number of failures."""
    print(f"\n=== {o.name} server ({o.url}) ===")
    if o.connect_error:
        print(f"  FAILED to connect: {o.connect_error}")
        return 1

    missing = o.tools_expected - o.tools_advertised
    extra = o.tools_advertised - o.tools_expected
    if missing:
        print(f"  MISSING advertised tools: {sorted(missing)}")
    if extra:
        print(f"  UNEXPECTED advertised tools: {sorted(extra)}")
    print(f"  {len(o.tools_advertised)} tools advertised")

    failures = 1 if missing else 0
    for c in o.calls:
        status = "PASS" if c.ok else "FAIL"
        if not c.ok:
            failures += 1
        print(f"  [{status}] {c.tool:<24} {c.elapsed*1000:7.0f} ms  {c.note}")
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ontology",
        default=os.getenv("MCP_ONTOLOGY_URL", "http://localhost:8766"),
        help="Base URL of the ontology MCP server",
    )
    parser.add_argument(
        "--sparql",
        default=os.getenv("MCP_SPARQL_URL", "http://localhost:8767"),
        help="Base URL of the SPARQL MCP server",
    )
    args = parser.parse_args()

    # Normalize to include /mcp path (streamable-HTTP default mount)
    def _url(base: str) -> str:
        return base if base.rstrip("/").endswith("/mcp") else base.rstrip("/") + "/mcp"

    ontology_url = _url(args.ontology)
    sparql_url = _url(args.sparql)

    print("AberOWL MCP test client")
    print(f"  ontology: {ontology_url}")
    print(f"  sparql:   {sparql_url}")

    ontology, sparql = await asyncio.gather(
        exercise_ontology_server(ontology_url),
        exercise_sparql_server(sparql_url),
    )

    failures = _print_outcome(ontology) + _print_outcome(sparql)

    total_calls = len(ontology.calls) + len(sparql.calls)
    passed = sum(1 for c in ontology.calls + sparql.calls if c.ok)
    print(f"\nSummary: {passed}/{total_calls} tool calls passed, {failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
