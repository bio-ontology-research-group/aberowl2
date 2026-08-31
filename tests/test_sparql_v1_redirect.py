"""
AberOWL 1 `/api/sparql` compatibility: rewrite and redirect (issue #102).

v1 did not execute SPARQL. Its engine returned the rewritten query plus the
endpoint named inside the query, and the Django layer 302-redirected the caller
there. Consumers use `requests`, which follows redirects by default, so they
received the endpoint's results transparently.

The v1 frame carries the endpoint; the v2 frame does not:

    v1:  VALUES ?x { OWL subeq <https://sparql.uniprot.org/sparql> <GO> { 'cell death' } }
    v2:  VALUES ?x { OWL subeq go-plus { 'cell death' } }

so the two must coexist without either capturing the other's queries.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

V1_VALUES = "VALUES ?x { OWL subeq <https://sparql.uniprot.org/sparql> <GO> { 'cell death' } }"
V1_FILTER = "FILTER ( ?x in ( OWL subclass <http://ontobee.org/sparql> <PATO> { quality } ) )"
V2_VALUES = "VALUES ?x { OWL subeq go-plus { 'cell death' } }"
PLAIN = "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"


class FakeRedis:
    def __init__(self):
        self._data = {}

    async def ping(self):
        return True

    async def hset(self, h, k, v):
        self._data.setdefault(h, {})[k] = v

    async def hget(self, h, k):
        return self._data.get(h, {}).get(k)

    async def hvals(self, h):
        return list(self._data.get(h, {}).values())

    async def hkeys(self, h):
        return list(self._data.get(h, {}).keys())

    async def close(self):
        pass


@pytest.mark.unit
class TestFrameDetection:
    """Pattern-level: the two syntaxes must not capture each other."""

    def test_v1_values_endpoint_is_found(self):
        from app.sparql_expander import find_v1_service_endpoint

        assert find_v1_service_endpoint(V1_VALUES) == "https://sparql.uniprot.org/sparql"

    def test_v1_filter_endpoint_is_found(self):
        from app.sparql_expander import find_v1_service_endpoint

        assert find_v1_service_endpoint(V1_FILTER) == "http://ontobee.org/sparql"

    def test_v2_frame_has_no_endpoint(self):
        from app.sparql_expander import find_v1_service_endpoint

        assert find_v1_service_endpoint(V2_VALUES) is None

    def test_plain_sparql_has_no_endpoint(self):
        from app.sparql_expander import find_v1_service_endpoint

        assert find_v1_service_endpoint(PLAIN) is None

    def test_v2_pattern_does_not_match_a_v1_frame(self):
        """The collision that would silently mis-parse every v1 query."""
        from app.sparql_expander import VALUES_OWL_PATTERN

        assert VALUES_OWL_PATTERN.search(V1_VALUES) is None
        assert VALUES_OWL_PATTERN.search(V2_VALUES) is not None

    def test_v1_pattern_does_not_match_a_v2_frame(self):
        from app.sparql_expander import VALUES_OWL_V1_PATTERN

        assert VALUES_OWL_V1_PATTERN.search(V2_VALUES) is None


@pytest.mark.unit
class TestV1FrameRewriting:

    @pytest.mark.asyncio
    async def test_v1_frame_is_expanded_to_iris(self, monkeypatch):
        from app import sparql_expander

        async def fake_dl(server_url, ontology_id, dl_query, query_type, timeout=30):
            return ["http://purl.obolibrary.org/obo/GO_0008219"], None

        monkeypatch.setattr(sparql_expander, "_run_dl_query", fake_dl)
        rewritten, expansions, errors = await sparql_expander.expand_sparql_query(
            V1_VALUES, {"go": "http://go-worker:80"})
        assert "GO_0008219" in rewritten
        assert "OWL subeq" not in rewritten
        assert errors == []
        assert expansions[0]["endpoint"] == "https://sparql.uniprot.org/sparql"

    @pytest.mark.asyncio
    async def test_v1_filter_frame_is_expanded(self, monkeypatch):
        from app import sparql_expander

        async def fake_dl(server_url, ontology_id, dl_query, query_type, timeout=30):
            return ["http://purl.obolibrary.org/obo/PATO_0000001"], None

        monkeypatch.setattr(sparql_expander, "_run_dl_query", fake_dl)
        rewritten, expansions, _ = await sparql_expander.expand_sparql_query(
            V1_FILTER, {"pato": "http://pato-worker:80"})
        assert "PATO_0000001" in rewritten
        assert expansions[0]["pattern"] == "FILTER(v1)"


@pytest.fixture
def client(monkeypatch):
    import app.main as main_module
    from app import sparql_expander
    from httpx import ASGITransport, AsyncClient

    redis = FakeRedis()
    redis._data[main_module.REGISTRY_KEY] = {
        "go": json.dumps({"ontology": "go", "url": "http://go-worker:80",
                          "status": "online", "title": "Gene Ontology"}),
    }
    main_module.redis_client = redis
    main_module.es_mgr = MagicMock()

    async def fake_dl(server_url, ontology_id, dl_query, query_type, timeout=30):
        return ["http://purl.obolibrary.org/obo/GO_0008219"], None

    monkeypatch.setattr(sparql_expander, "_run_dl_query", fake_dl)
    return AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test")


@pytest.mark.unit
class TestRedirectBehaviour:

    @pytest.mark.asyncio
    async def test_v1_call_redirects_to_the_named_endpoint(self, client):
        r = await client.get("/api/sparql", params={"query": V1_VALUES, "format": "json"})
        assert r.status_code == 302
        target = urlparse(r.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == "https://sparql.uniprot.org/sparql"

    @pytest.mark.asyncio
    async def test_the_redirect_carries_the_rewritten_query(self, client):
        r = await client.get("/api/sparql", params={"query": V1_VALUES, "format": "json"})
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert "GO_0008219" in q["query"][0]
        assert "OWL subeq" not in q["query"][0]
        assert q["format"] == ["json"]

    @pytest.mark.asyncio
    async def test_post_gets_a_location_header_not_a_redirect(self, client):
        """v1 answered POST with 200 + Location; a redirected POST loses its body."""
        r = await client.post("/api/sparql", json={"query": V1_VALUES},
                              params={"format": "json"})
        assert r.status_code == 200
        assert "sparql.uniprot.org" in r.headers["location"]

    @pytest.mark.asyncio
    async def test_missing_format_is_rejected_as_v1_did(self, client):
        r = await client.get("/api/sparql", params={"query": V1_VALUES})
        assert r.status_code == 400
        assert r.json()["message"] == "result format is required"

    @pytest.mark.asyncio
    async def test_v2_caller_still_gets_json(self, client):
        """No endpoint in the frame means a v2 caller: unchanged behaviour."""
        r = await client.get("/api/sparql", params={"query": V2_VALUES})
        assert r.status_code == 200
        assert "rewritten_query" in r.json()

    @pytest.mark.asyncio
    async def test_format_without_an_endpoint_explains_itself(self, client):
        """A v1-style call whose query names no endpoint cannot be redirected."""
        r = await client.get("/api/sparql", params={"query": PLAIN, "format": "json"})
        assert r.status_code == 400
        assert "endpoint" in r.json()["message"]
        assert "rewritten_query" in r.json()


@pytest.mark.unit
class TestRedirectRefusesToHideAFailure:
    """v1 redirected even when the frame did not resolve, sending an empty query
    to the endpoint. The caller then cannot tell "no matches" from "the reasoner
    was unreachable", which is the silent failure this whole effort exists to
    remove."""

    @pytest.fixture
    def client_with_broken_reasoner(self, monkeypatch):
        import app.main as main_module
        from app import sparql_expander
        from httpx import ASGITransport, AsyncClient

        redis = FakeRedis()
        redis._data[main_module.REGISTRY_KEY] = {
            "go": json.dumps({"ontology": "go", "url": "http://go-worker:80",
                              "status": "online", "title": "Gene Ontology"}),
        }
        main_module.redis_client = redis
        main_module.es_mgr = MagicMock()

        async def failing_dl(server_url, ontology_id, dl_query, query_type, timeout=30):
            return None, "worker unreachable"

        monkeypatch.setattr(sparql_expander, "_run_dl_query", failing_dl)
        return AsyncClient(transport=ASGITransport(app=main_module.app),
                           base_url="http://test")

    @pytest.mark.asyncio
    async def test_no_redirect_when_nothing_resolved(self, client_with_broken_reasoner):
        r = await client_with_broken_reasoner.get(
            "/api/sparql", params={"query": V1_VALUES, "format": "json"})
        assert r.status_code == 502
        assert "errors" in r.json()
        assert r.json()["errors"], "the reason must be reported"
