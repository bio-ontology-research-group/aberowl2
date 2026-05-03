"""
Unit tests for the SPARQL query rewriter.

These tests verify pattern matching and rewriting logic without
requiring a running ontology worker.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add central_server to path
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app.sparql_expander import (  # noqa: E402
    VALUES_OWL_PATTERN,
    FILTER_OWL_PATTERN,
    expand_sparql_query,
)


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPatternMatching:

    def test_values_pattern_basic(self):
        query = "VALUES ?class { OWL subeq GO { 'cell' } }"
        m = VALUES_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(1) == "?class"
        assert m.group(2) == "subeq"
        assert m.group(3) == "GO"
        assert m.group(4).strip() == "'cell'"

    def test_values_pattern_hyphenated_ontology_id(self):
        # Regression for issue #4: ontology ids with `-` or `.` were rejected.
        query = "VALUES ?c { OWL subeq go-plus { 'cell death' } }"
        m = VALUES_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(3) == "go-plus"

        query2 = "VALUES ?c { OWL subeq chebi.ext { 'small molecule' } }"
        m2 = VALUES_OWL_PATTERN.search(query2)
        assert m2 is not None
        assert m2.group(3) == "chebi.ext"

    def test_values_pattern_complex_dl(self):
        query = "VALUES ?x { OWL subclass HP { 'part of' some 'cell' } }"
        m = VALUES_OWL_PATTERN.search(query)
        assert m is not None
        assert "'part of' some 'cell'" in m.group(4)

    def test_values_pattern_in_full_sparql(self):
        query = """SELECT ?class ?label WHERE {
            VALUES ?class { OWL subeq GO { 'apoptotic process' } }
            ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
        }"""
        m = VALUES_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(3) == "GO"

    def test_filter_pattern_double_quotes(self):
        query = """FILTER OWL(?class, subeq, GO, "'part of' some 'cell'")"""
        m = FILTER_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(3) == "GO"
        assert m.group(4) == "'part of' some 'cell'"

    def test_filter_pattern_single_quotes(self):
        query = """FILTER OWL(?x, subclass, HP, 'cell')"""
        m = FILTER_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(4) == "cell"

    def test_filter_pattern_hyphenated_ontology_id(self):
        query = """FILTER OWL(?c, subeq, go-plus, "'cell death'")"""
        m = FILTER_OWL_PATTERN.search(query)
        assert m is not None
        assert m.group(3) == "go-plus"

    def test_no_match_on_plain_sparql(self):
        query = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10"
        assert VALUES_OWL_PATTERN.search(query) is None
        assert FILTER_OWL_PATTERN.search(query) is None


# ---------------------------------------------------------------------------
# Rewriting logic
# ---------------------------------------------------------------------------

def _ok(iris):
    """Helper: return the (iris, None) shape produced by _run_dl_query."""
    return AsyncMock(return_value=(iris, None))


def _err(message):
    """Helper: return the ([], error) shape produced by _run_dl_query."""
    return AsyncMock(return_value=([], message))


@pytest.mark.unit
class TestRewriting:

    @pytest.mark.asyncio
    async def test_values_expansion(self):
        query = "SELECT ?c WHERE { VALUES ?c { OWL subeq GO { 'cell' } } }"
        iris = [
            "http://purl.obolibrary.org/obo/GO_0005623",
            "http://purl.obolibrary.org/obo/GO_0005624",
        ]
        server_lookup = {"go": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _ok(iris)):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert errors == []
        assert len(expansions) == 1
        assert expansions[0]["pattern"] == "VALUES"
        assert expansions[0]["result_count"] == 2
        assert "<http://purl.obolibrary.org/obo/GO_0005623>" in rewritten
        assert "OWL" not in rewritten

    @pytest.mark.asyncio
    async def test_filter_expansion(self):
        query = """SELECT ?c WHERE {
            ?c a <http://www.w3.org/2002/07/owl#Class> .
            FILTER OWL(?c, subeq, HP, "'part of' some 'cell'")
        }"""
        iris = ["http://example.org/C1", "http://example.org/C2"]
        server_lookup = {"hp": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _ok(iris)):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert errors == []
        assert expansions[0]["pattern"] == "FILTER"
        assert "FILTER (?c IN (" in rewritten
        assert "<http://example.org/C1>" in rewritten

    @pytest.mark.asyncio
    async def test_hyphenated_ontology_id_expands(self):
        query = "VALUES ?c { OWL subeq go-plus { 'cell death' } }"
        iris = ["http://example.org/X"]
        server_lookup = {"go-plus": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _ok(iris)):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert errors == []
        assert len(expansions) == 1
        assert "<http://example.org/X>" in rewritten

    @pytest.mark.asyncio
    async def test_unknown_ontology_yields_error(self):
        query = "VALUES ?c { OWL subeq nonexistent { 'cell' } }"
        rewritten, expansions, errors = await expand_sparql_query(query, {"go": "http://fake"})

        assert expansions == []
        assert len(errors) == 1
        assert errors[0]["ontology"] == "nonexistent"
        assert "not registered" in errors[0]["error"] or "offline" in errors[0]["error"]
        # The offending frame must be replaced — the rewritten query should be
        # syntactically valid plain SPARQL (no leftover `OWL` token).
        assert "OWL" not in rewritten
        assert "VALUES ?c { }" in rewritten

    @pytest.mark.asyncio
    async def test_filter_unknown_ontology_replaced_with_false(self):
        query = """SELECT ?c WHERE {
            ?c a <http://www.w3.org/2002/07/owl#Class> .
            FILTER OWL(?c, subeq, nonexistent, "'cell'")
        }"""
        rewritten, expansions, errors = await expand_sparql_query(query, {})

        assert expansions == []
        assert len(errors) == 1
        assert "FILTER (false)" in rewritten

    @pytest.mark.asyncio
    async def test_dl_resolution_failure_yields_error(self):
        query = "VALUES ?c { OWL subeq GO { 'cell' } }"
        server_lookup = {"go": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _err("worker returned HTTP 500")):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert expansions == []
        assert len(errors) == 1
        assert errors[0]["error"] == "worker returned HTTP 500"

    @pytest.mark.asyncio
    async def test_no_frames_returns_query_unchanged(self):
        query = "SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }"
        rewritten, expansions, errors = await expand_sparql_query(query, {})
        assert rewritten == query
        assert expansions == []
        assert errors == []

    @pytest.mark.asyncio
    async def test_empty_dl_results(self):
        query = "VALUES ?c { OWL subeq GO { 'no_match' } }"
        server_lookup = {"go": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _ok([])):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert errors == []
        assert expansions[0]["result_count"] == 0
        assert "VALUES ?c {  }" in rewritten

    @pytest.mark.asyncio
    async def test_multiple_frames_independent(self):
        query = """SELECT ?a ?b WHERE {
            VALUES ?a { OWL subeq go-plus { 'cell death' } }
            VALUES ?b { OWL subeq nonexistent { 'whatever' } }
        }"""
        iris = ["http://example.org/A1"]
        server_lookup = {"go-plus": "http://fake-worker:8080"}

        with patch("app.sparql_expander._run_dl_query", _ok(iris)):
            rewritten, expansions, errors = await expand_sparql_query(query, server_lookup)

        assert len(expansions) == 1
        assert expansions[0]["ontology"] == "go-plus"
        assert len(errors) == 1
        assert errors[0]["ontology"] == "nonexistent"
        assert "<http://example.org/A1>" in rewritten
        assert "OWL" not in rewritten
