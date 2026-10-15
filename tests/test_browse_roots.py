"""
/api/browse_roots: where the class hierarchy starts.

An ontology that merges its imports gains their top-level classes as direct
subclasses of owl:Thing, so the browser's root list filled up with them. The
browser now asks /api/browse_roots, which returns the root terms the ontology
declares (IAO_0000700) when the worker has usable ones, and otherwise the
owl:Thing roots it showed before. DL queries are not affected.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

OWL_THING = "<http://www.w3.org/2002/07/owl#Thing>"
FLORA = {"class": "http://purl.obolibrary.org/obo/FLOPO_0000001", "label": "flora phenotype"}
CONTINUANT = {"class": "http://purl.obolibrary.org/obo/BFO_0000002", "label": "continuant"}
OCCURRENT = {"class": "http://purl.obolibrary.org/obo/BFO_0000003", "label": "occurrent"}

DECLARED_ANSWER = {
    "result": [dict(FLORA)],
    "source": "declared",
    "declared": [FLORA["class"], "http://example.org/missing"],
    "ignored": ["http://example.org/missing"],
}
THING_ANSWER = {"result": [dict(CONTINUANT), dict(FLORA), dict(OCCURRENT)]}


class FakeRedis:
    def __init__(self, entries):
        self._data = {"registered_servers": {e["ontology"]: json.dumps(e) for e in entries}}

    async def hvals(self, h):
        return list(self._data.get(h, {}).values())


def _server(status="online"):
    return {"ontology": "flopo", "title": "Flora Phenotype Ontology",
            "url": "http://flopo-worker:80", "status": status}


class _Resp:
    def __init__(self, status=200, payload=None, exc=None):
        self.status = status
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSession:
    """aiohttp.ClientSession stand-in keyed by the worker script name."""

    def __init__(self, worker=None):
        self.worker = worker or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, params=None, timeout=None):
        script = url.rsplit("/", 1)[-1]
        self.calls.append((script, dict(params or {})))
        return self.worker.get(script, _Resp(404, {"error": True}))


@pytest.fixture
def make_client(monkeypatch):
    import app.main as main_module
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(main_module, "es_mgr", MagicMock())

    def _make(session, servers=None):
        monkeypatch.setattr(main_module, "redis_client", FakeRedis(servers or [_server()]))
        monkeypatch.setattr(main_module.aiohttp, "ClientSession", lambda *a, **kw: session)
        return AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test")

    return _make


async def _get(client, path, **params):
    async with client:
        return await client.get(path, params=params)


@pytest.mark.unit
class TestDeclaredRoots:

    async def test_declared_roots_are_returned_and_tagged(self, make_client):
        session = FakeSession({"browseRoots.groovy": _Resp(200, DECLARED_ANSWER)})
        r = await _get(make_client(session), "/api/browse_roots", ontology="FLOPO")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "declared"
        assert [c["class"] for c in body["result"]] == [FLORA["class"]]
        assert body["result"][0]["ontology"] == "flopo"
        assert body["result"][0]["ontology_title"] == "Flora Phenotype Ontology"
        assert body["declared"] == DECLARED_ANSWER["declared"]
        assert body["ignored"] == ["http://example.org/missing"]
        # One request, to the registered id, and no owl:Thing query.
        assert session.calls == [("browseRoots.groovy", {"ontologyId": "flopo"})]

    async def test_worker_owl_thing_answer_passes_through(self, make_client):
        # An ontology without usable declared roots: the worker says so itself.
        answer = {"result": THING_ANSWER["result"], "source": "owl:Thing",
                  "declared": [], "ignored": []}
        session = FakeSession({"browseRoots.groovy": _Resp(200, answer)})
        body = (await _get(make_client(session), "/api/browse_roots", ontology="flopo")).json()
        assert body["source"] == "owl:Thing"
        assert len(body["result"]) == 3

    async def test_unknown_source_is_reported_as_owl_thing(self, make_client):
        session = FakeSession({"browseRoots.groovy": _Resp(200, {"result": [], "source": "bogus"})})
        body = (await _get(make_client(session), "/api/browse_roots", ontology="flopo")).json()
        assert body["source"] == "owl:Thing"


@pytest.mark.unit
class TestOwlThingFallback:

    @pytest.mark.parametrize("browse_resp", [
        _Resp(404, {"error": True}),                 # worker predates browseRoots.groovy
        _Resp(500, {"error": True}),
        _Resp(exc=asyncio.TimeoutError()),
        _Resp(exc=aiohttp.ClientConnectionError("refused")),
        _Resp(200, ["not", "a", "dict"]),
    ], ids=["404", "500", "timeout", "connection-error", "malformed"])
    async def test_falls_back_to_the_owl_thing_roots(self, make_client, browse_resp):
        session = FakeSession({"browseRoots.groovy": browse_resp,
                               "runQuery.groovy": _Resp(200, THING_ANSWER)})
        r = await _get(make_client(session), "/api/browse_roots", ontology="flopo")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "owl:Thing"
        assert {c["label"] for c in body["result"]} == {"continuant", "flora phenotype", "occurrent"}
        assert all(c["ontology"] == "flopo" for c in body["result"])
        script, params = session.calls[-1]
        assert script == "runQuery.groovy"
        assert params["query"] == OWL_THING
        assert params["type"] == "subclass"
        assert params["direct"] == "true"
        assert params["ontologyId"] == "flopo"

    async def test_both_failing_is_502(self, make_client):
        session = FakeSession({"browseRoots.groovy": _Resp(500, {}),
                               "runQuery.groovy": _Resp(500, {})})
        r = await _get(make_client(session), "/api/browse_roots", ontology="flopo")
        assert r.status_code == 502

    async def test_offline_ontology_is_404_without_worker_calls(self, make_client):
        session = FakeSession()
        r = await _get(make_client(session, [_server("offline")]), "/api/browse_roots", ontology="flopo")
        assert r.status_code == 404
        assert session.calls == []


@pytest.mark.unit
class TestDLQueriesStayComplete:

    async def test_owl_thing_dl_query_is_not_routed_to_browse_roots(self, make_client):
        # Declared roots are for browsing only: a DL query on owl:Thing still
        # gets every top-level class, imported ones included.
        session = FakeSession({"browseRoots.groovy": _Resp(200, DECLARED_ANSWER),
                               "runQuery.groovy": _Resp(200, THING_ANSWER)})
        r = await _get(make_client(session), "/api/dlquery_all", query=OWL_THING,
                       type="subclass", direct="true", ontologies="flopo")
        assert r.status_code == 200
        assert {c["label"] for c in r.json()["result"]} == {"continuant", "flora phenotype", "occurrent"}
        assert [c[0] for c in session.calls] == ["runQuery.groovy"]
