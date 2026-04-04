"""
Unit tests for MCP server tool definitions and explain_expansion logic.

These tests verify tool schemas and the explain_expansion tool
(which doesn't need a running server).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


# ---------------------------------------------------------------------------
# MCP Ontology Server
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPOntologyServer:

    def test_tool_definitions(self):
        from mcp_ontology_server import list_tools
        tools = asyncio.run(list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            "list_ontologies",
            "search_classes",
            "run_dl_query",
            "get_class_info",
            "get_ontology_info",
            "browse_hierarchy",
        }
        assert tool_names == expected, f"Missing tools: {expected - tool_names}"

    def test_search_classes_schema(self):
        from mcp_ontology_server import list_tools
        tools = asyncio.run(list_tools())
        search = next(t for t in tools if t.name == "search_classes")
        schema = search.inputSchema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]
        assert "ontology" in schema["properties"]

    def test_run_dl_query_schema(self):
        from mcp_ontology_server import list_tools
        tools = asyncio.run(list_tools())
        dl = next(t for t in tools if t.name == "run_dl_query")
        schema = dl.inputSchema
        assert "query" in schema["required"]
        assert "type" in schema["properties"]
        # type should have enum
        type_prop = schema["properties"]["type"]
        assert "enum" in type_prop
        assert "subeq" in type_prop["enum"]
        assert "subclass" in type_prop["enum"]

    def test_browse_hierarchy_schema(self):
        from mcp_ontology_server import list_tools
        tools = asyncio.run(list_tools())
        browse = next(t for t in tools if t.name == "browse_hierarchy")
        assert "class_iri" in browse.inputSchema["required"]
        assert "ontology" in browse.inputSchema["required"]

    def test_dl_query_description_has_syntax_guide(self):
        from mcp_ontology_server import list_tools
        tools = asyncio.run(list_tools())
        dl = next(t for t in tools if t.name == "run_dl_query")
        desc = dl.description.lower()
        assert "manchester" in desc
        assert "some" in desc  # Existential restriction example


# ---------------------------------------------------------------------------
# MCP SPARQL Server
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPSparqlServer:

    def test_tool_definitions(self):
        from mcp_sparql_server import list_tools
        tools = asyncio.run(list_tools())
        tool_names = {t.name for t in tools}
        expected = {"expand_sparql", "list_sparql_examples", "explain_expansion"}
        assert tool_names == expected, f"Missing tools: {expected - tool_names}"

    def test_expand_sparql_schema(self):
        from mcp_sparql_server import list_tools
        tools = asyncio.run(list_tools())
        expand = next(t for t in tools if t.name == "expand_sparql")
        assert "query" in expand.inputSchema["required"]
        assert "endpoint" in expand.inputSchema["properties"]

    def test_expand_sparql_description_has_patterns(self):
        from mcp_sparql_server import list_tools
        tools = asyncio.run(list_tools())
        expand = next(t for t in tools if t.name == "expand_sparql")
        desc = expand.description
        assert "VALUES" in desc
        assert "FILTER" in desc
        assert "OWL" in desc

    @pytest.mark.asyncio
    async def test_explain_expansion_values(self):
        from mcp_sparql_server import call_tool
        query = "VALUES ?c { OWL subeq GO { 'cell' } }"
        result = await call_tool("explain_expansion", {"query": query})
        assert len(result) == 1
        text = result[0].text
        assert "VALUES Expansion" in text
        assert "GO" in text
        assert "subeq" in text

    @pytest.mark.asyncio
    async def test_explain_expansion_filter(self):
        from mcp_sparql_server import call_tool
        query = """FILTER OWL(?x, subclass, HP, "'abnormal' and 'cell'")"""
        result = await call_tool("explain_expansion", {"query": query})
        text = result[0].text
        assert "FILTER Expansion" in text
        assert "HP" in text
        assert "subclass" in text

    @pytest.mark.asyncio
    async def test_explain_expansion_plain_sparql(self):
        from mcp_sparql_server import call_tool
        query = "SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }"
        result = await call_tool("explain_expansion", {"query": query})
        text = result[0].text
        assert "No OWL expansion patterns found" in text

    @pytest.mark.asyncio
    async def test_list_sparql_examples(self):
        from mcp_sparql_server import call_tool
        result = await call_tool("list_sparql_examples", {"ontology": "CHEBI"})
        text = result[0].text
        assert "CHEBI" in text
        assert "VALUES" in text
        assert "FILTER" in text
