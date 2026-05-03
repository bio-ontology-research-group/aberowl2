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
    """Extract the text payload from a FastMCP call_tool result.

    Newer FastMCP returns ``(content_list, structured_dict)``; older
    versions returned ``content_list`` directly.
    """
    assert result, "call_tool returned no content"
    content = result[0] if isinstance(result, tuple) else result
    first = content[0]
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
            "rewrite_sparql",
            "list_sparql_examples",
            "query_sparql",
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
# MCP Ontology Server — rewrite_sparql tool
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRewriteSparqlTool:

    async def test_rewrite_sparql_schema(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        rewrite = next(t for t in tools if t.name == "rewrite_sparql")
        assert "query" in rewrite.inputSchema["required"]

    async def test_rewrite_sparql_description_has_patterns(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        rewrite = next(t for t in tools if t.name == "rewrite_sparql")
        desc = rewrite.description
        assert "VALUES" in desc
        assert "FILTER" in desc
        assert "OWL" in desc

    async def test_rewrite_sparql_formats_success(self):
        import mcp_ontology_server as srv
        payload = {
            "rewritten_query": "SELECT ?c WHERE { VALUES ?c { <http://x/1> <http://x/2> } }",
            "expansions": [{
                "pattern": "VALUES", "variable": "?c", "type": "subeq",
                "ontology": "go-plus", "dl_query": "'cell death'", "result_count": 2,
            }],
            "errors": [],
        }
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool(
                "rewrite_sparql",
                {"query": "SELECT ?c WHERE { VALUES ?c { OWL subeq go-plus { 'cell death' } } }"},
            )
        text = _text(result)
        assert "Resolved 1 OWL frame" in text
        assert "go-plus" in text
        assert "Rewritten query:" in text
        assert "<http://x/1>" in text

    async def test_rewrite_sparql_reports_per_frame_error(self):
        import mcp_ontology_server as srv
        payload = {
            "rewritten_query": "SELECT ?c WHERE { VALUES ?c {  } }",
            "expansions": [],
            "errors": [{
                "pattern": "VALUES", "variable": "?c", "type": "subeq",
                "ontology": "nonexistent", "dl_query": "'x'",
                "error": "ontology 'nonexistent' is not registered or its worker is offline",
            }],
        }
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool(
                "rewrite_sparql",
                {"query": "VALUES ?c { OWL subeq nonexistent { 'x' } }"},
            )
        text = _text(result)
        assert "Could not resolve 1 frame" in text
        assert "nonexistent" in text
        assert "not registered" in text

    async def test_rewrite_sparql_passes_through_plain_query(self):
        import mcp_ontology_server as srv
        plain = "SELECT ?s WHERE { ?s ?p ?o }"
        payload = {"rewritten_query": plain, "expansions": [], "errors": []}
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=payload)):
            result = await srv.mcp.call_tool("rewrite_sparql", {"query": plain})
        text = _text(result)
        assert "No OWL DL frames found" in text
        assert plain in text


# ---------------------------------------------------------------------------
# MCP Ontology Server — query_sparql tool
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQuerySparqlTool:

    async def test_query_sparql_schema(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        q = next(t for t in tools if t.name == "query_sparql")
        assert "query" in q.inputSchema["required"]
        assert "endpoint" in q.inputSchema["properties"]

    async def test_query_sparql_default_endpoint_is_ontobee(self):
        from mcp_ontology_server import mcp
        tools = await mcp.list_tools()
        q = next(t for t in tools if t.name == "query_sparql")
        assert "sparql.hegroup.org" in q.description.lower() or "ontobee" in q.description.lower()

    async def test_query_sparql_runs_rewritten_query(self):
        import mcp_ontology_server as srv
        rewrite_payload = {
            "rewritten_query": "SELECT ?c WHERE { VALUES ?c { <http://x/1> } }",
            "expansions": [{
                "pattern": "VALUES", "variable": "?c", "type": "subeq",
                "ontology": "pizza", "dl_query": "Pizza", "result_count": 1,
            }],
            "errors": [],
        }
        endpoint_results = {
            "head": {"vars": ["c"]},
            "results": {"bindings": [
                {"c": {"type": "uri", "value": "http://x/1"}},
            ]},
        }
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=rewrite_payload)), \
             patch.object(srv, "_execute_sparql", new=AsyncMock(return_value=endpoint_results)) as exec_mock:
            result = await srv.mcp.call_tool(
                "query_sparql",
                {"query": "SELECT ?c WHERE { VALUES ?c { OWL subeq pizza { Pizza } } }"},
            )
        text = _text(result)
        assert "sparql.hegroup.org" in text  # default endpoint
        assert "Resolved 1 OWL frame" in text
        assert "1 row" in text
        assert "c=http://x/1" in text
        # default endpoint passed through to _execute_sparql
        called_endpoint = exec_mock.call_args[0][0]
        assert called_endpoint == srv.DEFAULT_SPARQL_ENDPOINT

    async def test_query_sparql_uses_custom_endpoint(self):
        import mcp_ontology_server as srv
        rewrite_payload = {"rewritten_query": "ASK {}", "expansions": [], "errors": []}
        endpoint_results = {"boolean": True, "head": {}}
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=rewrite_payload)), \
             patch.object(srv, "_execute_sparql", new=AsyncMock(return_value=endpoint_results)) as exec_mock:
            result = await srv.mcp.call_tool(
                "query_sparql",
                {"query": "ASK {}", "endpoint": "https://example.org/sparql"},
            )
        text = _text(result)
        assert "https://example.org/sparql" in text
        assert "ASK result: True" in text
        assert exec_mock.call_args[0][0] == "https://example.org/sparql"

    async def test_query_sparql_reports_endpoint_error(self):
        import mcp_ontology_server as srv
        rewrite_payload = {"rewritten_query": "SELECT * WHERE {}", "expansions": [], "errors": []}
        with patch.object(srv, "_api_post", new=AsyncMock(return_value=rewrite_payload)), \
             patch.object(srv, "_execute_sparql", new=AsyncMock(return_value={"error": "endpoint returned HTTP 503: down"})):
            result = await srv.mcp.call_tool(
                "query_sparql", {"query": "SELECT * WHERE {}"},
            )
        text = _text(result)
        assert "Endpoint error" in text
        assert "HTTP 503" in text
        # The rewritten query is included so the user can re-run manually.
        assert "Rewritten query" in text
