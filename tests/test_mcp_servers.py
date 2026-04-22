"""
Unit tests for MCP server tool definitions and tool behavior.

These tests verify tool schemas and call each tool with mocked HTTP
responses. No central server / Docker is required.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(result):
    """Extract the text payload from a FastMCP call_tool result."""
    assert result, "call_tool returned no content"
    first = result[0]
    return first.text if hasattr(first, "text") else first["text"]


# ---------------------------------------------------------------------------
# MCP Ontology Server — schemas
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPOntologyServerSchemas:

    async def test_tool_definitions(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
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

    async def test_search_classes_schema(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        search = next(t for t in tools if t.name == "search_classes")
        schema = search.inputSchema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]
        assert "ontology" in schema["properties"]

    async def test_run_dl_query_schema(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        dl = next(t for t in tools if t.name == "run_dl_query")
        schema = dl.inputSchema
        assert "query" in schema["required"]
        assert "type" in schema["properties"]

    async def test_browse_hierarchy_schema(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        browse = next(t for t in tools if t.name == "browse_hierarchy")
        assert "class_iri" in browse.inputSchema["required"]
        assert "ontology" in browse.inputSchema["required"]

    async def test_dl_query_description_has_syntax_guide(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        dl = next(t for t in tools if t.name == "run_dl_query")
        desc = dl.description.lower()
        assert "manchester" in desc
        assert "some" in desc  # existential restriction example


# ---------------------------------------------------------------------------
# MCP Ontology Server — tool execution with mocked HTTP
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPOntologyServerCalls:

    async def test_list_ontologies_formats_output(self):
        import mcp_ontology_server as srv
        payload = {"result": [
            {"id": "go", "title": "Gene Ontology", "status": "online"},
            {"id": "hp", "title": "Human Phenotype", "status": "offline"},
        ]}
        with patch.object(srv, "_api_get", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool("list_ontologies", {})
        text = _text(result)
        assert "Found 2 ontologies" in text
        assert "[ONLINE] go" in text
        assert "[offline] hp" in text

    async def test_search_classes_passes_ontology_filter(self):
        import mcp_ontology_server as srv
        payload = {"result": [
            {"label": "apoptotic process", "class": "http://purl.obolibrary.org/obo/GO_0006915",
             "ontology": "go", "definition": "A form of programmed cell death."},
        ]}
        mock = AsyncMock(return_value=payload)
        with patch.object(srv, "_api_get", new=mock):
            result = await srv.mcp.call_tool(
                "search_classes", {"query": "apoptosis", "ontology": "go"}
            )
        text = _text(result)
        assert "Found 1 results for 'apoptosis'" in text
        assert "apoptotic process" in text
        # Verify ontology filter was forwarded to API
        _, kwargs = mock.call_args
        passed_params = mock.call_args[0][1] if len(mock.call_args[0]) > 1 else kwargs.get("params", {})
        # arg order is (path, params)
        assert mock.call_args[0][0] == "/api/search_all"
        assert mock.call_args[0][1]["ontologies"] == "go"

    async def test_search_classes_no_results(self):
        import mcp_ontology_server as srv
        with patch.object(srv, "_api_get", new=AsyncMock(return_value={"result": []})):
            result = await srv.mcp.call_tool("search_classes", {"query": "nothing"})
        assert "No results found for 'nothing'" in _text(result)

    async def test_run_dl_query_formats_output(self):
        import mcp_ontology_server as srv
        payload = {"result": [
            {"label": "mitochondrion", "class": "http://purl.obolibrary.org/obo/GO_0005739", "ontology": "go"},
        ]}
        mock = AsyncMock(return_value=payload)
        with patch.object(srv, "_api_get", new=mock):
            result = await srv.mcp.call_tool(
                "run_dl_query",
                {"query": "'part of' some 'cell'", "type": "subeq", "ontology": "go"},
            )
        text = _text(result)
        assert "Found 1 results for subeq query" in text
        assert "mitochondrion" in text
        params = mock.call_args[0][1]
        assert params["type"] == "subeq"
        assert params["ontologies"] == "go"
        assert params["labels"] == "true"

    async def test_get_class_info_returns_json(self):
        import mcp_ontology_server as srv
        payload = {"class": "http://purl.obolibrary.org/obo/GO_0005623", "label": "cell"}
        with patch.object(srv, "_api_get", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool(
                "get_class_info",
                {"class_iri": "http://purl.obolibrary.org/obo/GO_0005623", "ontology": "go"},
            )
        text = _text(result)
        assert '"label": "cell"' in text

    async def test_get_class_info_reports_error(self):
        import mcp_ontology_server as srv
        with patch.object(srv, "_api_get", new=AsyncMock(return_value={"error": "HTTP 404: not found"})):
            result = await srv.mcp.call_tool(
                "get_class_info", {"class_iri": "bad", "ontology": "go"}
            )
        assert "Error: HTTP 404" in _text(result)

    async def test_get_ontology_info_returns_json(self):
        import mcp_ontology_server as srv
        payload = {"id": "go", "title": "Gene Ontology", "class_count": 50000}
        with patch.object(srv, "_api_get", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool("get_ontology_info", {"ontology": "go"})
        text = _text(result)
        assert '"id": "go"' in text
        assert '"class_count": 50000' in text

    async def test_browse_hierarchy_translates_owl_thing(self):
        import mcp_ontology_server as srv
        payload = {"result": [
            {"label": "top-level thing", "class": "http://example.org/X", "ontology": "go"},
        ]}
        mock = AsyncMock(return_value=payload)
        with patch.object(srv, "_api_get", new=mock):
            result = await srv.mcp.call_tool(
                "browse_hierarchy",
                {"class_iri": "owl:Thing", "ontology": "go", "direction": "subclass"},
            )
        text = _text(result)
        assert "Direct subclasses" in text
        assert "top-level thing" in text
        params = mock.call_args[0][1]
        assert params["query"] == "<http://www.w3.org/2002/07/owl#Thing>"
        assert params["type"] == "subclass"


# ---------------------------------------------------------------------------
# MCP SPARQL Server — schemas
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPSparqlServerSchemas:

    async def test_tool_definitions(self):
        from mcp_sparql_server import mcp
        tools = await mcp.list_tools()
        tool_names = {t.name for t in tools}
        expected = {"expand_sparql", "list_sparql_examples", "explain_expansion"}
        assert tool_names == expected, f"Missing tools: {expected - tool_names}"

    async def test_expand_sparql_schema(self):
        from mcp_sparql_server import mcp
        tools = await mcp.list_tools()
        expand = next(t for t in tools if t.name == "expand_sparql")
        assert "query" in expand.inputSchema["required"]
        assert "endpoint" in expand.inputSchema["properties"]

    async def test_expand_sparql_description_has_patterns(self):
        from mcp_sparql_server import mcp
        tools = await mcp.list_tools()
        expand = next(t for t in tools if t.name == "expand_sparql")
        desc = expand.description
        assert "VALUES" in desc
        assert "FILTER" in desc
        assert "OWL" in desc


# ---------------------------------------------------------------------------
# MCP SPARQL Server — tool execution
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMCPSparqlServerCalls:

    async def test_expand_sparql_formats_bindings(self):
        import mcp_sparql_server as srv
        payload = {
            "expansions": [{
                "pattern": "VALUES", "variable": "?c", "type": "subeq",
                "ontology": "go", "result_count": 3,
            }],
            "results": {
                "head": {"vars": ["c", "label"]},
                "results": {"bindings": [
                    {"c": {"value": "http://example.org/A"}, "label": {"value": "thing A"}},
                    {"c": {"value": "http://example.org/B"}, "label": {"value": "thing B"}},
                ]},
            },
        }
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool("expand_sparql", {"query": "SELECT * WHERE {}"})
        text = _text(result)
        assert "OWL Expansions Applied" in text
        assert "subeq query on go -> 3 classes" in text
        assert "Results (2 rows)" in text
        assert "thing A" in text

    async def test_expand_sparql_reports_error(self):
        import mcp_sparql_server as srv
        with patch.object(srv, "_api_post", new=AsyncMock(return_value={"error": "HTTP 500: boom"})):
            result = await srv.mcp.call_tool("expand_sparql", {"query": "broken"})
        assert "Error: HTTP 500" in _text(result)

    async def test_list_sparql_examples_uses_ontology(self):
        from mcp_sparql_server import mcp
        result = await mcp.call_tool("list_sparql_examples", {"ontology": "CHEBI"})
        text = _text(result)
        assert "CHEBI" in text
        assert "VALUES" in text
        assert "FILTER" in text

    async def test_explain_expansion_values(self):
        from mcp_sparql_server import mcp
        result = await mcp.call_tool(
            "explain_expansion", {"query": "VALUES ?c { OWL subeq GO { 'cell' } }"}
        )
        text = _text(result)
        assert "VALUES Expansion" in text
        assert "GO" in text
        assert "subeq" in text

    async def test_explain_expansion_filter(self):
        from mcp_sparql_server import mcp
        result = await mcp.call_tool(
            "explain_expansion",
            {"query": "FILTER OWL(?x, subclass, HP, \"'abnormal' and 'cell'\")"},
        )
        text = _text(result)
        assert "FILTER Expansion" in text
        assert "HP" in text
        assert "subclass" in text

    async def test_explain_expansion_plain_sparql(self):
        from mcp_sparql_server import mcp
        result = await mcp.call_tool(
            "explain_expansion",
            {"query": "SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }"},
        )
        assert "No OWL expansion patterns found" in _text(result)
