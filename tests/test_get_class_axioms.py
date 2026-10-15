"""
/api/getClass shows a class's logical axioms, not only its annotations.

The ES class documents are built without axioms, so a class served from ES had
no SubClassOf or EquivalentTo in its details even when the ontology asserts
them. getClass now takes those fields from the ontology's worker, for exactly
the requested IRI, and falls back to the ES document whenever the worker cannot
answer.
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

IRI = "http://example.org/onto/C_1"
EQUIV_IRI = "http://example.org/onto/C_2"
PARENT_IRI = "http://example.org/onto/P_1"

INDEXED = {
    "class": IRI,
    "ontology": "go",
    "label": "indexed label",
    "definition": ["indexed definition"],
    "synonyms": ["indexed synonym"],
    "oboid": "C:1",
    "comment": ["an extra annotation only ES holds"],
}

# What the worker's toInfo returns for IRI with axioms=true.
WORKER_RECORD = {
    "class": IRI,
    "owlClass": f"<{IRI}>",
    "ontology": "go",
    "label": "worker label",
    "definition": ["worker definition"],
    "worker_only_annotation": ["must not leak into the ES document"],
    "SubClassOf": ["<a href='#'>parent</a>"],
    "Equivalent": ["'part of' some <a href='#'>whole</a>"],
    "Disjoint": ["<a href='#'>other</a>"],
}

# A different class in the same equivalence node, with its own axioms.
EQUIV_RECORD = {
    "class": EQUIV_IRI,
    "owlClass": f"<{EQUIV_IRI}>",
    "ontology": "go",
    "label": "equivalent class",
    "SubClassOf": ["axiom of the equivalent class"],
    "Equivalent": ["definition of the equivalent class"],
}


class FakeRedis:
    def __init__(self, entries):
        self._data = {"registered_servers": {e["ontology"]: json.dumps(e) for e in entries}}

    async def hvals(self, h):
        return list(self._data.get(h, {}).values())


class BrokenRedis:
    async def hvals(self, h):
        raise ConnectionError("redis down")


def _server(status="online"):
    return {"ontology": "go", "title": "GO", "url": "http://go-worker:80", "status": status}


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
    """aiohttp.ClientSession stand-in for both the ES search and the worker.

    `worker` maps a runQuery `type` to a _Resp; a type not listed answers 500.
    """

    def __init__(self, es_hits=None, es_exc=None, worker=None):
        self.es_hits = es_hits or []
        self.es_exc = es_exc
        self.worker = worker or {}
        self.worker_calls = []
        self.es_queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, timeout=None):
        self.es_queries.append(json)
        return _Resp(200, {"hits": {"hits": [{"_source": h} for h in self.es_hits]}},
                     exc=self.es_exc)

    def get(self, url, params=None, timeout=None):
        self.worker_calls.append((url, dict(params or {}), timeout))
        return self.worker.get((params or {}).get("type"), _Resp(500, {}))


@pytest.fixture
def make_client(monkeypatch):
    import app.main as main_module
    from httpx import ASGITransport, AsyncClient

    es = MagicMock()
    es.es_url = "http://es:9200"
    es._alias_name = lambda oid: f"aberowl_{oid}_classes"
    monkeypatch.setattr(main_module, "es_mgr", es)

    def _make(session, redis=None):
        monkeypatch.setattr(main_module, "redis_client", redis or FakeRedis([_server()]))
        monkeypatch.setattr(main_module.aiohttp, "ClientSession", lambda *a, **kw: session)
        transport = ASGITransport(app=main_module.app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _make


async def _get_class(client, query=IRI, ontology="go"):
    async with client:
        return await client.get("/api/getClass", params={"query": query, "ontology": ontology})


# ---------------------------------------------------------------------------
# ES document + worker axioms
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestIndexedClassGetsAxioms:

    async def test_axioms_are_added_to_the_indexed_document(self, make_client):
        session = FakeSession(es_hits=[INDEXED],
                              worker={"equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        body = r.json()
        assert body["SubClassOf"] == WORKER_RECORD["SubClassOf"]
        assert body["Equivalent"] == WORKER_RECORD["Equivalent"]
        assert body["Disjoint"] == WORKER_RECORD["Disjoint"]

    async def test_indexed_annotations_are_kept(self, make_client):
        session = FakeSession(es_hits=[INDEXED],
                              worker={"equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        body = (await _get_class(make_client(session))).json()
        for key, value in INDEXED.items():
            assert body[key] == value, key
        # Nothing but the logical fields comes from the worker.
        assert "worker_only_annotation" not in body
        assert "owlClass" not in body
        assert set(body) == set(INDEXED) | {"SubClassOf", "Equivalent", "Disjoint"}

    async def test_worker_request_is_exact_and_bounded(self, make_client):
        import app.main as main_module
        session = FakeSession(es_hits=[INDEXED],
                              worker={"equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        # The caller's id is upper case; the worker is asked with the registered id.
        await _get_class(make_client(session), ontology="GO")
        assert len(session.worker_calls) == 1
        url, params, timeout = session.worker_calls[0]
        assert url == "http://go-worker:80/api/runQuery.groovy"
        assert params["query"] == f"<{IRI}>"
        assert params["type"] == "equivalent"
        assert params["axioms"] == "true"
        assert params["ontologyId"] == "go"
        assert timeout.total == main_module.GETCLASS_AXIOM_TIMEOUT

    async def test_bracketed_query_matches_the_bare_iri(self, make_client):
        session = FakeSession(es_hits=[INDEXED],
                              worker={"equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        body = (await _get_class(make_client(session), query=f"<{IRI}>")).json()
        # ES is searched by the bare IRI, so the indexed annotations are found.
        assert session.es_queries == [
            {"query": {"bool": {"must": [{"term": {"class": IRI}}]}}, "size": 1}]
        for key, value in INDEXED.items():
            assert body[key] == value, key
        assert body["SubClassOf"] == WORKER_RECORD["SubClassOf"]
        assert session.worker_calls[0][1]["query"] == f"<{IRI}>"


@pytest.mark.unit
class TestExactIriMatching:

    async def test_record_for_the_queried_iri_is_picked_from_the_node(self, make_client):
        # The equivalent class comes first in the worker answer.
        session = FakeSession(es_hits=[INDEXED], worker={
            "equivalent": _Resp(200, {"result": [EQUIV_RECORD, WORKER_RECORD]})})
        body = (await _get_class(make_client(session))).json()
        assert body["SubClassOf"] == WORKER_RECORD["SubClassOf"]
        assert body["Equivalent"] == WORKER_RECORD["Equivalent"]

    async def test_equivalent_class_axioms_are_never_attributed(self, make_client):
        # The node holds only another class (e.g. the queried one is filtered
        # by the worker as deprecated): the ES document is served unchanged.
        session = FakeSession(es_hits=[INDEXED], worker={
            "equivalent": _Resp(200, {"result": [EQUIV_RECORD]})})
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        assert r.json() == INDEXED
        # No superclass query either: its rows describe other classes.
        assert [c[1]["type"] for c in session.worker_calls] == ["equivalent"]


@pytest.mark.unit
class TestFallbackToIndexedDocument:

    @pytest.mark.parametrize("worker_resp", [
        _Resp(exc=asyncio.TimeoutError()),
        _Resp(exc=aiohttp.ClientConnectionError("refused")),
        _Resp(500, {"error": True}),
        _Resp(404, {"error": True, "message": "Ontology not found: go"}),
        _Resp(200, {"result": []}),
        _Resp(200, ["not", "a", "dict"]),
    ], ids=["timeout", "connection-error", "500", "404", "empty", "malformed"])
    async def test_worker_failure_serves_the_indexed_document(self, make_client, worker_resp):
        session = FakeSession(es_hits=[INDEXED], worker={"equivalent": worker_resp})
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        assert r.json() == INDEXED

    async def test_offline_worker_is_not_queried(self, make_client):
        session = FakeSession(es_hits=[INDEXED],
                              worker={"equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        r = await _get_class(make_client(session, FakeRedis([_server("offline")])))
        assert r.status_code == 200
        assert r.json() == INDEXED
        assert session.worker_calls == []

    async def test_registry_failure_serves_the_indexed_document(self, make_client):
        session = FakeSession(es_hits=[INDEXED])
        r = await _get_class(make_client(session, BrokenRedis()))
        assert r.status_code == 200
        assert r.json() == INDEXED

    async def test_indexed_logical_fields_kept_without_worker_answer(self, make_client):
        indexed = {**INDEXED, "SubClassOf": ["indexed parent"]}
        session = FakeSession(es_hits=[indexed], worker={"equivalent": _Resp(500, {})})
        body = (await _get_class(make_client(session))).json()
        assert body["SubClassOf"] == ["indexed parent"]

    async def test_worker_answer_replaces_indexed_logical_fields(self, make_client):
        indexed = {**INDEXED, "SubClassOf": ["stale parent"], "Equivalent": ["stale"]}
        record = {k: v for k, v in WORKER_RECORD.items() if k not in ("Equivalent", "Disjoint")}
        session = FakeSession(es_hits=[indexed],
                              worker={"equivalent": _Resp(200, {"result": [record]})})
        body = (await _get_class(make_client(session))).json()
        assert body["SubClassOf"] == WORKER_RECORD["SubClassOf"]
        # The worker states the class has no equivalence axiom.
        assert "Equivalent" not in body


# ---------------------------------------------------------------------------
# No ES document: the worker alone
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWorkerOnlyFallback:

    async def test_exact_worker_record_is_returned(self, make_client):
        session = FakeSession(worker={
            "equivalent": _Resp(200, {"result": [EQUIV_RECORD, WORKER_RECORD]})})
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        assert r.json() == WORKER_RECORD

    async def test_es_error_falls_back_to_worker(self, make_client):
        session = FakeSession(es_exc=aiohttp.ClientConnectionError("es down"), worker={
            "equivalent": _Resp(200, {"result": [WORKER_RECORD]})})
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        assert r.json() == WORKER_RECORD

    async def test_equivalent_class_is_not_returned_for_the_queried_iri(self, make_client):
        parent_row = {"class": PARENT_IRI, "label": "parent",
                      "SubClassOf": ["axiom of the parent"], "Equivalent": ["parent def"]}
        session = FakeSession(worker={
            "equivalent": _Resp(200, {"result": [EQUIV_RECORD]}),
            "superclass": _Resp(200, {"result": [parent_row]}),
        })
        r = await _get_class(make_client(session))
        assert r.status_code == 200
        body = r.json()
        assert body["class"] == IRI
        # The superclass is listed as a parent; its own axioms are not copied.
        assert body["SubClassOf"] == ["parent"]
        assert "Equivalent" not in body
        assert "axiom of the equivalent class" not in json.dumps(body)

    async def test_unknown_class_is_404(self, make_client):
        session = FakeSession(worker={
            "equivalent": _Resp(200, {"result": []}),
            "superclass": _Resp(200, {"result": []}),
        })
        r = await _get_class(make_client(session))
        assert r.status_code == 404

    async def test_offline_ontology_is_404(self, make_client):
        session = FakeSession()
        r = await _get_class(make_client(session, FakeRedis([_server("offline")])))
        assert r.status_code == 404
        assert session.worker_calls == []
