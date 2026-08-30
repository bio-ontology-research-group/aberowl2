"""
The worker-backed AberOWL 1 operations (issue #94, step 3).

Deliberately small. The worker call itself is stubbed — what matters here is
the routing, the v1 envelope, and the `/service/api/` allow-list, none of which
need a real reasoner. End-to-end behaviour is verified on beta against real
workers instead.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


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


@pytest.fixture
def app_module():
    import app.main as main_module

    redis = FakeRedis()
    redis._data[main_module.REGISTRY_KEY] = {
        "go": json.dumps({
            "ontology": "go", "ontology_id": "go", "title": "Gene Ontology",
            "url": "http://go-worker:80", "status": "online", "class_count": 47000,
        }),
        "down": json.dumps({
            "ontology": "down", "ontology_id": "down", "title": "Offline",
            "url": "http://down-worker:80", "status": "offline",
        }),
    }
    main_module.redis_client = redis
    main_module.es_mgr = MagicMock()
    return main_module


@pytest.fixture
def client(app_module):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app_module.app), base_url="http://test")


@pytest.fixture
def stub_worker(monkeypatch):
    """Record what would be sent to a worker, and reply with a canned result."""
    from app import api_v1

    calls = []

    async def fake(server, script, params):
        calls.append({"script": script, "params": params, "url": server.get("url")})
        return {"result": [{"owlClass": "<http://x/GO_1>", "label": "cell"}]}

    monkeypatch.setattr(api_v1, "_call_worker", fake)
    return calls


@pytest.mark.unit
class TestDLQuery:

    @pytest.mark.asyncio
    async def test_requires_query_and_type(self, client):
        assert (await client.get("/api/dlquery")).json()["message"] == "query is required"
        r = await client.get("/api/dlquery", params={"query": "cell"})
        assert r.json()["message"] == "type is required"

    @pytest.mark.asyncio
    async def test_returns_the_v1_envelope(self, client, stub_worker):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO"})).json()
        assert body["status"] == "ok"
        assert body["total"] == len(body["result"]) == 1
        assert stub_worker[0]["script"] == "runQuery.groovy"

    @pytest.mark.asyncio
    async def test_uppercase_acronym_resolves(self, client, stub_worker):
        """v1 callers pass uppercase; registry ids are lowercase."""
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO"})).json()
        assert body["status"] == "ok"
        assert stub_worker[0]["params"]["ontologyId"] == "go"

    @pytest.mark.asyncio
    async def test_offline_ontology_reports_v1_style(self, client, stub_worker):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "down"})).json()
        assert body["status"] == "exception"


@pytest.mark.unit
class TestRootAndObjectProperties:

    @pytest.mark.asyncio
    async def test_root_passes_the_iri_through(self, client, stub_worker):
        iri = "http://purl.obolibrary.org/obo/GO_0008150"
        body = (await client.get(f"/api/ontology/GO/root/{iri}")).json()
        assert body["status"] == "ok"
        assert stub_worker[0]["script"] == "findRoot.groovy"
        assert stub_worker[0]["params"]["query"] == iri

    @pytest.mark.asyncio
    async def test_root_repairs_a_collapsed_scheme(self, client, stub_worker):
        """Proxies collapse `http://` in a path to `http:/`; v1 repaired it too."""
        await client.get("/api/ontology/GO/root/http:/purl.obolibrary.org/obo/GO_1")
        assert stub_worker[0]["params"]["query"].startswith("http://")

    @pytest.mark.asyncio
    async def test_object_property_list(self, client, stub_worker):
        body = (await client.get("/api/ontology/GO/objectproperty")).json()
        assert body["status"] == "ok"
        assert stub_worker[0]["script"] == "getObjectProperties.groovy"
        assert "property" not in stub_worker[0]["params"]

    @pytest.mark.asyncio
    async def test_single_object_property(self, client, stub_worker):
        await client.get("/api/ontology/GO/objectproperty/http://purl.obolibrary.org/obo/BFO_0000050")
        assert stub_worker[0]["params"]["property"].endswith("BFO_0000050")


@pytest.mark.unit
class TestMatchSuperClasses:

    @pytest.mark.asyncio
    async def test_requires_both_class_sets(self, client):
        r = await client.post("/api/ontology/GO/class/_matchsuperclasses", json={})
        assert "source_classes" in r.json()["message"]

    @pytest.mark.asyncio
    async def test_returns_a_result_map(self, client, stub_worker):
        r = await client.post("/api/ontology/GO/class/_matchsuperclasses",
                              json={"source_classes": ["http://x/A"],
                                    "target_classes": ["http://x/B"]})
        assert "result" in r.json()


@pytest.mark.unit
class TestServiceApiAllowList:

    @pytest.mark.asyncio
    async def test_read_only_servlet_is_forwarded(self, client, stub_worker):
        body = (await client.get("/service/api/runQuery.groovy", params={
            "query": "cell", "type": "subeq", "ontology": "GO"})).json()
        assert "result" in body
        assert stub_worker[0]["script"] == "runQuery.groovy"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("script", [
        "addOntology.groovy", "removeOntology.groovy", "updateOntology.groovy",
        "reloadOntology.groovy", "triggerIndexing.groovy", "elastic.groovy",
    ])
    async def test_mutating_servlets_are_refused(self, client, stub_worker, script):
        """The whole point of the allow-list: these must never be proxied."""
        body = (await client.get(f"/service/api/{script}", params={"ontology": "GO"})).json()
        assert body["status"] == "error"
        assert stub_worker == [], f"{script} reached a worker"


@pytest.mark.unit
class TestDLQueryPagination:
    """`offset` is a 1-based page number, as in v1.

    Ignoring it would be worse than rejecting it: a caller looping until an
    empty page would get the full set every time and never terminate.
    """

    @pytest.fixture
    def many_results(self, monkeypatch):
        from app import api_v1

        async def fake(server, script, params):
            return {"result": [{"owlClass": f"<http://x/C{i}>"} for i in range(25)]}

        monkeypatch.setattr(api_v1, "_call_worker", fake)

    @pytest.mark.asyncio
    async def test_page_one_returns_the_first_page(self, client, many_results):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO", "offset": 1})).json()
        assert len(body["result"]) == 10
        assert body["result"][0]["owlClass"] == "<http://x/C0>"
        assert body["total"] == 25  # total is the full count, not the page

    @pytest.mark.asyncio
    async def test_later_page_advances(self, client, many_results):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO", "offset": 3})).json()
        assert len(body["result"]) == 5
        assert body["result"][0]["owlClass"] == "<http://x/C20>"

    @pytest.mark.asyncio
    async def test_page_past_the_end_is_empty_so_a_loop_terminates(self, client, many_results):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO", "offset": 9})).json()
        assert body["result"] == []

    @pytest.mark.asyncio
    async def test_without_offset_the_full_set_comes_back(self, client, many_results):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO"})).json()
        assert len(body["result"]) == 25

    @pytest.mark.asyncio
    async def test_a_nonsense_offset_is_rejected(self, client, many_results):
        body = (await client.get("/api/dlquery", params={
            "query": "cell", "type": "subeq", "ontology": "GO", "offset": "abc"})).json()
        assert body["status"] == "error"
