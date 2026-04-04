"""
Unit tests for the SPARQL query expander.

These tests verify pattern matching and query rewriting logic
without requiring a running SPARQL endpoint or ontology server.
"""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add central_server to path
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app.sparql_expander import (
    VALUES_OWL_PATTERN,
    FILTER_OWL_PATTERN,
    expand_sparql_query,
    execute_sparql,
)


# ---------------------------------------------------------------------------
# Pattern matching tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPatternMatching:

    def test_values_pattern_matches_basic(self):
        query = "VALUES ?class { OWL subeq GO { 'cell' } }"
        match = VALUES_OWL_PATTERN.search(query)
        assert match is not None
        assert match.group(1) == "?class"
        assert match.group(2) == "subeq"
        assert match.group(3) == "GO"
        assert match.group(4).strip() == "'cell'"

    def test_values_pattern_matches_complex_dl_query(self):
        query = "VALUES ?x { OWL subclass HP { 'part of' some 'cell' } }"
        match = VALUES_OWL_PATTERN.search(query)
        assert match is not None
        assert match.group(1) == "?x"
        assert match.group(2) == "subclass"
        assert match.group(3) == "HP"
        assert "'part of' some 'cell'" in match.group(4)

    def test_values_pattern_in_full_sparql(self):
        query = """SELECT ?class ?label WHERE {
            VALUES ?class { OWL subeq GO { 'apoptotic process' } }
            ?class <http://www.w3.org/2000/01/rdf-schema#label> ?label .
        }"""
        match = VALUES_OWL_PATTERN.search(query)
        assert match is not None
        assert match.group(3) == "GO"

    def test_filter_pattern_matches_double_quotes(self):
        query = """FILTER OWL(?class, subeq, GO, "'part of' some 'cell'")"""
        match = FILTER_OWL_PATTERN.search(query)
        assert match is not None
        assert match.group(1) == "?class"
        assert match.group(2) == "subeq"
        assert match.group(3) == "GO"
        assert match.group(4) == "'part of' some 'cell'"

    def test_filter_pattern_matches_single_quotes(self):
        query = """FILTER OWL(?x, subclass, HP, 'cell')"""
        match = FILTER_OWL_PATTERN.search(query)
        assert match is not None
        assert match.group(1) == "?x"
        assert match.group(2) == "subclass"
        assert match.group(3) == "HP"
        assert match.group(4) == "cell"

    def test_no_match_on_plain_sparql(self):
        query = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10"
        assert VALUES_OWL_PATTERN.search(query) is None
        assert FILTER_OWL_PATTERN.search(query) is None


# ---------------------------------------------------------------------------
# Expansion logic tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExpansion:

    @pytest.mark.asyncio
    async def test_values_expansion(self):
        query = "SELECT ?c WHERE { VALUES ?c { OWL subeq GO { 'cell' } } }"
        mock_iris = [
            "http://purl.obolibrary.org/obo/GO_0005623",
            "http://purl.obolibrary.org/obo/GO_0005624",
        ]
        server_lookup = {"go": "http://fake-server:8080"}

        with patch("app.sparql_expander._run_dl_query", new_callable=AsyncMock, return_value=mock_iris):
            expanded, expansions = await expand_sparql_query(query, server_lookup)

        assert len(expansions) == 1
        assert expansions[0]["pattern"] == "VALUES"
        assert expansions[0]["result_count"] == 2
        assert "<http://purl.obolibrary.org/obo/GO_0005623>" in expanded
        assert "<http://purl.obolibrary.org/obo/GO_0005624>" in expanded
        assert "OWL" not in expanded  # Pattern should be replaced

    @pytest.mark.asyncio
    async def test_filter_expansion(self):
        query = """SELECT ?c WHERE {
            ?c a <http://www.w3.org/2002/07/owl#Class> .
            FILTER OWL(?c, subeq, HP, "'part of' some 'cell'")
        }"""
        mock_iris = ["http://example.org/C1", "http://example.org/C2"]
        server_lookup = {"hp": "http://fake-server:8080"}

        with patch("app.sparql_expander._run_dl_query", new_callable=AsyncMock, return_value=mock_iris):
            expanded, expansions = await expand_sparql_query(query, server_lookup)

        assert len(expansions) == 1
        assert expansions[0]["pattern"] == "FILTER"
        assert "FILTER (?c IN (" in expanded
        assert "<http://example.org/C1>" in expanded

    @pytest.mark.asyncio
    async def test_no_expansion_on_plain_sparql(self):
        query = "SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> }"
        expanded, expansions = await expand_sparql_query(query, {})
        assert expanded == query
        assert len(expansions) == 0

    @pytest.mark.asyncio
    async def test_missing_server_skips_expansion(self):
        query = "VALUES ?c { OWL subeq NONEXISTENT { 'cell' } }"
        expanded, expansions = await expand_sparql_query(query, {"go": "http://fake"})
        # Pattern remains unchanged because server not found
        assert "OWL" in expanded
        assert len(expansions) == 0

    @pytest.mark.asyncio
    async def test_empty_dl_results(self):
        query = "VALUES ?c { OWL subeq GO { 'nonexistent_class_xyz' } }"
        server_lookup = {"go": "http://fake-server:8080"}

        with patch("app.sparql_expander._run_dl_query", new_callable=AsyncMock, return_value=[]):
            expanded, expansions = await expand_sparql_query(query, server_lookup)

        assert len(expansions) == 1
        assert expansions[0]["result_count"] == 0
        assert "VALUES ?c {  }" in expanded
