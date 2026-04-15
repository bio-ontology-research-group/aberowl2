"""
Unit tests for the central server FastAPI endpoints.

Uses httpx TestClient with a test FastAPI app to avoid needing
Docker for most tests. Tests that need real ES/Virtuoso/ontology
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
    main_module.virtuoso_mgr = MagicMock()

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

    @pytest.mark.asyncio
    async def test_sparql_plain_query(self, client, test_app):
        """Plain SPARQL (no OWL patterns) should pass through."""
        with patch("app.main.execute_sparql", new_callable=AsyncMock, return_value={
            "head": {"vars": ["s"]},
            "results": {"bindings": [{"s": {"type": "uri", "value": "http://example.org/A"}}]},
        }):
            r = await client.get("/api/sparql", params={
                "query": "SELECT ?s WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> } LIMIT 1"
            })
            assert r.status_code == 200
            body = r.json()
            assert "results" in body
            assert body.get("expansions") is None  # No expansions for plain SPARQL


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
