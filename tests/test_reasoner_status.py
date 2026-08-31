"""
The reasoner outcome reaches the registry and the v1 API (issue #94, step 4).

Workers have always reported it: `getStatistics.groovy` returns
`manager.getStatus(ontologyId)` — one of RequestManager.loadStati's values
(loading / loaded / classified / incoherent). Central fetched it on every poll
and then overwrote it, because the registry uses `status` for the *serving*
state. It is now kept as `reasoner_status`.

AberOWL 1's `/api/ontology/` reported exactly this, in its own vocabulary
(Classified / Incoherent / Unloadable / Unknown), so the v1 layer maps it rather
than reporting "Unknown" for everything.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


def _entry(**over):
    e = {"ontology": "go", "ontology_id": "go", "title": "Gene Ontology",
         "url": "http://go-worker:80", "status": "online", "class_count": 47000}
    e.update(over)
    return e


class FakeRedis:
    def __init__(self, entries=None):
        self._data = {}
        if entries:
            self._data["registered_servers"] = {
                e["ontology"]: json.dumps(e) for e in entries}

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
class TestStatusMapping:
    """Worker vocabulary to v1 vocabulary."""

    @pytest.mark.parametrize("worker,expected", [
        ("classified", "Classified"),
        ("incoherent", "Incoherent"),
        ("loaded", "Unknown"),      # in memory, classification unfinished
        ("loading", "Unknown"),
        ("unknown", "Unknown"),
        ("", "Unknown"),
    ])
    def test_online_ontology(self, worker, expected):
        from app.api_v1 import _v1_status

        assert _v1_status(_entry(reasoner_status=worker)) == expected

    def test_offline_worker_is_unloadable(self):
        """v1's Unloadable: the ontology exists but could not be served."""
        from app.api_v1 import _v1_status

        assert _v1_status(_entry(status="offline", reasoner_status="classified")) == "Unloadable"

    def test_missing_reasoner_status_is_unknown_not_classified(self):
        """Never infer Classified from a worker merely being online."""
        from app.api_v1 import _v1_status

        assert _v1_status(_entry()) == "Unknown"

    def test_an_unrecognised_value_is_unknown(self):
        from app.api_v1 import _v1_status

        assert _v1_status(_entry(reasoner_status="something-new")) == "Unknown"


@pytest.mark.unit
class TestRegistryKeepsTheReasonerStatus:

    @pytest.mark.asyncio
    async def test_poll_no_longer_discards_it(self, monkeypatch):
        """The defect: `server.update(stats)` then `server["status"] = "online"`
        overwrote the worker's reasoner outcome on every poll."""
        import app.main as main_module

        server = {"ontology": "go", "url": "http://go-worker:80"}

        class FakeResponse:
            status = 200

            async def json(self):
                return {"status": "classified", "class_count": 47000,
                        "reasoner_type": "elk"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class FakeSession:
            def get(self, *a, **k):
                return FakeResponse()

            async def close(self):
                pass

        main_module.redis_client = FakeRedis()
        await main_module.fetch_and_update_server_metadata(server, session=FakeSession())

        assert server["reasoner_status"] == "classified"
        assert server["status"] == "online", "serving state must still be the serving state"


@pytest.mark.unit
class TestV1EndpointReportsIt:

    @pytest.fixture
    def client(self):
        import app.main as main_module
        from httpx import ASGITransport, AsyncClient

        main_module.redis_client = FakeRedis([
            _entry(ontology="go", reasoner_status="classified"),
            _entry(ontology="bad", title="Incoherent one", reasoner_status="incoherent"),
            _entry(ontology="down", title="Offline one", status="offline"),
        ])
        main_module.es_mgr = MagicMock()
        return AsyncClient(transport=ASGITransport(app=main_module.app),
                           base_url="http://test")

    @pytest.mark.asyncio
    async def test_each_ontology_reports_its_own_outcome(self, client):
        records = {e["acronym"]: e["status"]
                   for e in (await client.get("/api/ontology/")).json()}
        assert records["GO"] == "Classified"
        assert records["BAD"] == "Incoherent"
        assert records["DOWN"] == "Unloadable"

    @pytest.mark.asyncio
    async def test_values_stay_inside_the_v1_vocabulary(self, client):
        allowed = {"Classified", "Incoherent", "Unloadable", "Unknown"}
        for e in (await client.get("/api/ontology/")).json():
            assert e["status"] in allowed
