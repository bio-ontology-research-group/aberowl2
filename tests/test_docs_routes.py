"""
/docs belongs to the SPA; Swagger moves to /api/docs (issue #93).

Both used to claim /docs. A direct request got Swagger; clicking "Docs" in the
site nav routed client-side and rendered the SPA page without any request
reaching the server. Same URL, two different pages depending on how you arrived
— which is how the REST API came to look as though it had been replaced by MCP
(biopragmatics/bioregistry#2030).

Charles Hoyt's report on #93 was specifically "see docs at
http://aber-owl.net/docs", so the page a direct visit lands on is the thing
being fixed.
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
        "go": json.dumps({"ontology": "go", "url": "http://go:80",
                          "status": "online", "title": "Gene Ontology"}),
    }
    main_module.redis_client = redis
    main_module.es_mgr = MagicMock()
    return main_module


@pytest.fixture
def client(app_module):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app_module.app), base_url="http://test")


@pytest.mark.unit
class TestDocsRouting:

    def test_swagger_is_not_on_slash_docs(self, app_module):
        """The collision: FastAPI must not own /docs any more."""
        assert app_module.app.docs_url == "/api/docs"
        assert app_module.app.redoc_url == "/api/redoc"

    def test_no_registered_route_claims_slash_docs(self, app_module):
        """Anything matching /docs exactly would shadow the SPA again."""
        claimed = [r.path for r in app_module.app.routes
                   if getattr(r, "path", None) == "/docs"]
        assert claimed == [], f"/docs is claimed by {claimed}"

    @pytest.mark.asyncio
    async def test_swagger_answers_on_its_new_path(self, client):
        r = await client.get("/api/docs")
        assert r.status_code == 200
        assert "swagger" in r.text.lower()

    @pytest.mark.asyncio
    async def test_openapi_json_is_unchanged(self, client):
        """Robert linked this URL on the Bioregistry issue; it must not move."""
        r = await client.get("/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        assert "paths" in spec

    @pytest.mark.asyncio
    async def test_the_spec_still_lists_the_endpoints_people_asked_for(self, client):
        """The complaint was that these looked gone. They must be discoverable."""
        paths = (await client.get("/openapi.json")).json()["paths"]
        for p in ("/api/listOntologies", "/api/getOntology", "/api/search_all",
                  "/api/dlquery_all", "/api/getClass"):
            assert p in paths, f"{p} missing from the OpenAPI spec"

    @pytest.mark.asyncio
    async def test_the_restored_v1_paths_are_in_the_spec_too(self, client):
        paths = (await client.get("/openapi.json")).json()["paths"]
        for p in ("/api/ontology/", "/api/dlquery", "/api/class/_find"):
            assert p in paths, f"{p} missing from the OpenAPI spec"
