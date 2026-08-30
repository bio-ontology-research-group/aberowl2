"""
Retired v1 operations, and /api/sparql telling the truth (issue #94, step 6).

This is the only part of the compatibility work that changes behaviour a caller
sees today, so each case pins the *old* wrong behaviour as much as the new one:

  * `/api/sparql` answered HTTP 200 with the caller's own query echoed back as
    `rewritten_query`. AberOWL 1 executed SPARQL; AberOWL 2 only rewrites. A
    silent no-op is the worst available failure, so a v1-style call now gets 501.
  * `_similar` and `dlquery/logs` fell through to the SPA catch-all and answered
    HTTP 200 with a web page. They now answer 410.
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
def client():
    import app.main as main_module
    from httpx import ASGITransport, AsyncClient

    redis = FakeRedis()
    redis._data[main_module.REGISTRY_KEY] = {
        "go": json.dumps({"ontology": "go", "url": "http://go-worker:80",
                          "status": "online", "title": "Gene Ontology"}),
    }
    main_module.redis_client = redis
    main_module.es_mgr = MagicMock()
    return AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test")


PLAIN_SPARQL = "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"


@pytest.mark.unit
class TestSparqlIsHonest:

    @pytest.mark.asyncio
    async def test_v1_style_call_gets_501(self, client):
        """v1 required `format` and executed the query. We do not execute."""
        r = await client.get("/api/sparql", params={"query": PLAIN_SPARQL, "format": "json"})
        assert r.status_code == 501
        body = r.json()
        assert body["status"] == "error"
        assert "does not execute" in body["message"]

    @pytest.mark.asyncio
    async def test_501_still_carries_the_rewrite(self, client):
        """Failing loudly should still hand back something usable."""
        r = await client.get("/api/sparql", params={"query": PLAIN_SPARQL, "format": "json"})
        assert "rewritten_query" in r.json()

    @pytest.mark.asyncio
    async def test_result_format_also_counts_as_a_v1_call(self, client):
        r = await client.get("/api/sparql", params={"query": PLAIN_SPARQL, "result_format": "json"})
        assert r.status_code == 501

    @pytest.mark.asyncio
    async def test_v2_caller_is_unaffected(self, client):
        """No `format` parameter means a v2 caller: still 200, still a rewrite."""
        r = await client.get("/api/sparql", params={"query": PLAIN_SPARQL})
        assert r.status_code == 200
        assert "rewritten_query" in r.json()

    @pytest.mark.asyncio
    async def test_a_frameless_query_says_nothing_was_resolved(self, client):
        """The silent no-op: without a frame the query comes back byte-identical."""
        body = (await client.get("/api/sparql", params={"query": PLAIN_SPARQL})).json()
        assert body["rewritten_query"] == PLAIN_SPARQL
        assert "warning" in body


@pytest.mark.unit
class TestRetiredOperations:

    @pytest.mark.asyncio
    async def test_similar_is_gone_not_html(self, client):
        r = await client.get("/api/class/_similar",
                             params={"ontology": "GO", "class": "cell", "size": 5})
        assert r.status_code == 410
        assert r.json()["status"] == "error"
        assert "_find" in r.json()["message"]  # points at the replacement

    @pytest.mark.asyncio
    async def test_dlquery_logs_is_gone_not_html(self, client):
        r = await client.get("/api/dlquery/logs")
        assert r.status_code == 410
        assert r.json()["status"] == "error"

    @pytest.mark.asyncio
    async def test_retired_paths_do_not_shadow_live_ones(self, client):
        """/api/dlquery must still work despite /api/dlquery/logs being retired."""
        r = await client.get("/api/dlquery")
        assert r.status_code == 200
        assert r.json()["message"] == "query is required"
