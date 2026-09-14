"""
Unit tests for the central server FastAPI endpoints.

Uses httpx TestClient with a test FastAPI app to avoid needing
Docker for most tests. Tests that need real ES/ontology
containers are marked as 'slow'.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


class FakeRedis:
    """Minimal async Redis mock."""

    def __init__(self):
        self._data = {}

    async def ping(self):
        return True

    async def hset(self, hash_name, key, value):
        self._data.setdefault(hash_name, {})[key] = value

    async def hget(self, hash_name, key):
        return self._data.get(hash_name, {}).get(key)

    async def hdel(self, hash_name, key):
        if hash_name in self._data and key in self._data[hash_name]:
            del self._data[hash_name][key]
            return 1
        return 0

    async def hvals(self, hash_name):
        return list(self._data.get(hash_name, {}).values())

    async def hkeys(self, hash_name):
        return list(self._data.get(hash_name, {}).keys())

    async def close(self):
        pass


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def populated_redis(fake_redis):
    """Redis with some registered servers."""
    servers = {
        "go": json.dumps({
            "ontology": "go",
            "title": "Gene Ontology",
            "url": "http://go-server:80",
            "status": "online",
            "class_count": 47000,
            "property_count": 100,
            "object_property_count": 50,
            "individual_count": 0,
        }),
        "hp": json.dumps({
            "ontology": "hp",
            "title": "Human Phenotype Ontology",
            "url": "http://hp-server:80",
            "status": "online",
            "class_count": 16000,
            "property_count": 30,
            "object_property_count": 20,
            "individual_count": 0,
        }),
        "test_offline": json.dumps({
            "ontology": "test_offline",
            "title": "Offline Ontology",
            "url": "http://offline:80",
            "status": "offline",
            "class_count": 100,
            "property_count": 5,
        }),
    }
    fake_redis._data["registered_servers"] = servers
    return fake_redis


@pytest.fixture
def test_app(populated_redis):
    """Create a test FastAPI app with mocked dependencies."""
    # We need to patch the redis_client and es_mgr before importing main
    import app.main as main_module

    main_module.redis_client = populated_redis
    main_module.es_mgr = MagicMock()

    from httpx import ASGITransport, AsyncClient
    return main_module.app


@pytest.fixture
def client(test_app):
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=test_app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# listOntologies
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestListOntologies:

    @pytest.mark.asyncio
    async def test_list_ontologies(self, client):
        r = await client.get("/api/listOntologies")
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        ids = {o["id"] for o in body["result"]}
        assert "go" in ids
        assert "hp" in ids
        assert "test_offline" in ids

    @pytest.mark.asyncio
    async def test_list_ontologies_has_status(self, client):
        r = await client.get("/api/listOntologies")
        for ont in r.json()["result"]:
            assert "status" in ont
            assert "title" in ont


# ---------------------------------------------------------------------------
# getOntology
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetOntology:

    @pytest.mark.asyncio
    async def test_get_ontology_found(self, client):
        r = await client.get("/api/getOntology", params={"ontology": "go"})
        assert r.status_code == 200
        body = r.json()
        assert body["ontology"] == "go"
        assert body["title"] == "Gene Ontology"

    @pytest.mark.asyncio
    async def test_get_ontology_case_insensitive(self, client):
        r = await client.get("/api/getOntology", params={"ontology": "GO"})
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_get_ontology_not_found(self, client):
        r = await client.get("/api/getOntology", params={"ontology": "nonexistent"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# getStats
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetStats:

    @pytest.mark.asyncio
    async def test_get_stats_aggregate(self, client):
        r = await client.get("/api/getStats")
        assert r.status_code == 200
        body = r.json()
        assert body["total_ontologies"] == 3
        assert body["online_ontologies"] == 2
        assert body["total_classes"] == 47000 + 16000 + 100

    @pytest.mark.asyncio
    async def test_get_stats_single_ontology(self, client):
        r = await client.get("/api/getStats", params={"ontology": "go"})
        assert r.status_code == 200
        body = r.json()
        assert body["ontology"] == "go"
        assert body["class_count"] == 47000

    @pytest.mark.asyncio
    async def test_get_stats_not_found(self, client):
        r = await client.get("/api/getStats", params={"ontology": "nonexistent"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# getStatuses
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetStatuses:

    @pytest.mark.asyncio
    async def test_get_statuses(self, client):
        r = await client.get("/api/getStatuses")
        assert r.status_code == 200
        body = r.json()
        assert body["go"] == "online"
        assert body["hp"] == "online"
        assert body["test_offline"] == "offline"


# ---------------------------------------------------------------------------
# queryNames
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQueryNames:

    @pytest.mark.asyncio
    async def test_query_names_missing_term(self, client):
        r = await client.get("/api/queryNames")
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_query_names_with_results(self, client, test_app):
        import app.main as main_module
        main_module.es_mgr.search_classes = AsyncMock(return_value=[
            {"class": "http://example.org/C1", "label": "cell", "ontology": "go"},
        ])
        r = await client.get("/api/queryNames", params={"term": "cell"})
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        assert len(body["result"]) == 1
        main_module.es_mgr.search_classes.assert_called_once()


# ---------------------------------------------------------------------------
# search_all (direct ES)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSearchAll:

    @pytest.mark.asyncio
    async def test_search_all_missing_query(self, client):
        r = await client.get("/api/search_all")
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_search_all_with_results(self, client, test_app):
        import app.main as main_module
        main_module.es_mgr.search_classes = AsyncMock(return_value=[
            {"class": "http://example.org/C1", "label": "test", "ontology": "go"},
        ])
        r = await client.get("/api/search_all", params={"query": "test"})
        assert r.status_code == 200
        assert len(r.json()["result"]) == 1


# ---------------------------------------------------------------------------
# getClass
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetClass:

    @pytest.mark.asyncio
    async def test_get_class_missing_params(self, client):
        r = await client.get("/api/getClass")
        assert r.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# SPARQL expansion endpoint
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSparqlEndpoint:

    @pytest.mark.asyncio
    async def test_sparql_missing_query(self, client):
        r = await client.get("/api/sparql")
        assert r.status_code == 400

    # NOTE: there is deliberately no test here for executing a plain SPARQL
    # query. AberOWL rewrites SPARQL, it never executes it — /api/sparql returns
    # {rewritten_query, expansions, errors} and the caller runs the result
    # against whatever endpoint they choose. Passthrough of a query carrying no
    # OWL frames is covered in tests/test_sparql_expander.py
    # (test_no_match_on_plain_sparql, test_no_frames_returns_query_unchanged).


# ---------------------------------------------------------------------------
# API key management (admin)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestAPIKeyAdmin:

    @pytest.mark.asyncio
    async def test_create_api_key_unauthorized(self, client):
        r = await client.post("/admin/api_keys", json={"name": "test"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_create_api_key_authorized(self, client):
        import base64
        creds = base64.b64encode(b"admin:changeme").decode()
        r = await client.post(
            "/admin/api_keys",
            json={"name": "test-key", "description": "A test key"},
            headers={"Authorization": f"Basic {creds}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "test-key"
        assert body["key"].startswith("aberowl_")

    @pytest.mark.asyncio
    async def test_list_api_keys(self, client):
        import base64
        creds = base64.b64encode(b"admin:changeme").decode()
        # Create a key first
        await client.post(
            "/admin/api_keys",
            json={"name": "list-test"},
            headers={"Authorization": f"Basic {creds}"},
        )
        r = await client.get(
            "/admin/api_keys",
            headers={"Authorization": f"Basic {creds}"},
        )
        assert r.status_code == 200
        keys = r.json()
        assert isinstance(keys, list)


# ---------------------------------------------------------------------------
# dlquery_all — cap reporting
# ---------------------------------------------------------------------------

class _FakeResponse:
    """One worker's reply to a DL query."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    """aiohttp.ClientSession stand-in: replies per worker URL."""

    def __init__(self, by_url):
        self._by_url = by_url
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        for fragment, payload in self._by_url.items():
            if fragment in url:
                return _FakeResponse(payload)
        return _FakeResponse({"result": []}, status=500)


@pytest.mark.unit
class TestDLQueryAllCapReporting:
    """A worker cuts its answer at the reasoner result limit and says so with
    `capped`; the aggregate must carry that through instead of dropping it, or
    len(result) reads as a count when it is only a lower bound (#126)."""

    def _patch_session(self, by_url):
        import app.main as main_module
        session = _FakeSession(by_url)
        return session, patch.object(
            main_module.aiohttp, "ClientSession", lambda *a, **kw: session
        )

    @pytest.mark.asyncio
    async def test_capped_worker_is_reported(self, client):
        session, patched = self._patch_session({
            "go-server": {"result": [{"class": "http://example.org/A", "label": "a"}],
                          "capped": True},
            "hp-server": {"result": [{"class": "http://example.org/B", "label": "b"}],
                          "capped": False},
        })
        with patched:
            r = await client.get("/api/dlquery_all",
                                 params={"query": "'cell'", "type": "subeq"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["result"]) == 2          # shape unchanged: one flat list
        assert body["capped"] is True
        assert body["capped_ontologies"] == ["go"]

    @pytest.mark.asyncio
    async def test_uncapped_and_legacy_workers_report_complete(self, client):
        # `capped` absent is a worker predating the field: a complete answer.
        session, patched = self._patch_session({
            "go-server": {"result": [{"class": "http://example.org/A", "label": "a"}]},
            "hp-server": {"result": [{"class": "http://example.org/B", "label": "b"}],
                          "capped": False},
        })
        with patched:
            r = await client.get("/api/dlquery_all",
                                 params={"query": "'cell'", "type": "subeq"})
        body = r.json()
        assert body["capped"] is False
        assert "capped_ontologies" not in body
        assert len(body["result"]) == 2

    @pytest.mark.asyncio
    async def test_direct_is_forwarded_to_the_worker(self, client):
        # browse_hierarchy relies on this: direct=true must reach the worker.
        session, patched = self._patch_session({
            "go-server": {"result": []}, "hp-server": {"result": []},
        })
        with patched:
            r = await client.get("/api/dlquery_all",
                                 params={"query": "'cell'", "type": "subclass",
                                         "ontologies": "go", "direct": "true"})
        assert r.status_code == 200
        assert session.calls, "no worker was queried"
        url, params = session.calls[0]
        assert url.endswith("/api/runQuery.groovy")
        assert params["direct"] == "true"
        assert params["ontologyId"] == "go"
