"""Live end-to-end tests for the AberOWL MCP ontology server.

These drive a RUNNING MCP server over its streamable-HTTP endpoint — any MCP
client can (the Claude Code UI is just one). They are deliberately NOT unit
tests: they need a live server with GO indexed, hit the network, and depend on
external state. So they are SKIPPED unless ``ABEROWL_MCP_URL`` is set, which
keeps CI green while still giving a repeatable on-demand check:

    ABEROWL_MCP_URL=https://beta.aber-owl.net/mcp/ontology/mcp \\
        pytest tests/test_mcp_live.py -v

The mocked-HTTP unit tests live in ``test_mcp_servers.py``; this file is the
integration counterpart.
"""

import os
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession

try:  # the client was renamed in newer mcp releases
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
except ImportError:  # pragma: no cover - older mcp
    from mcp.client.streamable_http import streamablehttp_client

MCP_URL = os.getenv("ABEROWL_MCP_URL")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not MCP_URL, reason="set ABEROWL_MCP_URL to run the live MCP e2e tests"
    ),
]

# Stable fixtures — assume the target server has GO indexed (the canonical test
# ontology). "apoptosis" is an exact synonym of GO_0006915 ("apoptotic process").
APOPTOSIS_IRI = "http://purl.obolibrary.org/obo/GO_0006915"

EXPECTED_TOOLS = {
    "list_ontologies", "search_classes", "find_iri", "run_dl_query",
    "get_class_info", "get_ontology_info", "browse_hierarchy",
    "rewrite_sparql", "list_sparql_examples", "query_sparql",
}


# A fresh connection per test, entered/exited in the test's own task (avoids the
# anyio "exit cancel scope in a different task" error that a shared async
# fixture triggers under pytest-asyncio).
@asynccontextmanager
async def mcp_session():
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call(session, tool, args):
    result = await session.call_tool(tool, args)
    return result.content[0].text


async def test_tool_discovery():
    async with mcp_session() as mcp:
        names = {t.name for t in (await mcp.list_tools()).tools}
    assert "find_iri" in names
    assert "lookup_iri" not in names  # renamed
    assert names == EXPECTED_TOOLS


async def test_find_iri_by_label():
    async with mcp_session() as mcp:
        out = await _call(mcp, "find_iri", {"term": "apoptosis", "ontology": "go"})
    assert "GO_0006915" in out


async def test_find_iri_by_curie():
    async with mcp_session() as mcp:
        out = await _call(mcp, "find_iri", {"term": "GO:0006915"})
    assert "GO_0006915" in out


async def test_find_iri_validates_iri():
    async with mcp_session() as mcp:
        out = await _call(mcp, "find_iri", {"term": APOPTOSIS_IRI, "ontology": "go"})
    assert "GO_0006915" in out


async def test_find_iri_nonexistent_is_not_resolved():
    async with mcp_session() as mcp:
        out = await _call(mcp, "find_iri", {"term": "zzqxnotarealterm", "ontology": "go"})
    assert ("Could not resolve" in out) or ("No exact match" in out)
    assert "GO_0006915" not in out


async def test_search_classes_returns_hits():
    async with mcp_session() as mcp:
        out = await _call(mcp, "search_classes", {"query": "apoptosis", "ontology": "go"})
    assert "GO_" in out


async def test_get_class_info():
    async with mcp_session() as mcp:
        out = await _call(mcp, "get_class_info", {"class_iri": APOPTOSIS_IRI, "ontology": "go"})
    assert "GO_0006915" in out


async def test_run_dl_query_reasoner():
    # Exercises the worker reasoner (DL query), not just ES.
    async with mcp_session() as mcp:
        out = await _call(
            mcp, "run_dl_query", {"query": "'apoptotic process'", "type": "subclass", "ontology": "go"}
        )
    assert "GO_" in out
