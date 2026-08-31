"""
/api/listOntologies carries what a registry needs (issue #93).

Charles Hoyt named this endpoint specifically on #93:

    "http://aber-owl.net/api/listOntologies is missing most of the fields that
     used to be available. I used to pull out: prefix, name, status, acronym,
     homepage, description, version, download URL. now, I only see the prefix,
     name, and status"

It returned id/title/status, so a harvester needed a second request per
ontology — about 971 of them — to get anything else.

`id`, `title` and `status` keep their names and meanings; everything else is
additive, so existing callers are unaffected.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

# The fields named in the report, mapped onto what this endpoint calls them.
REPORTED_FIELDS = ["id", "name", "status", "homepage", "description",
                   "version", "download_url"]


class FakeRedis:
    def __init__(self, entries):
        self._data = {"registered_servers": {
            e["ontology"]: json.dumps(e) for e in entries}}

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
def corpus(tmp_path, monkeypatch):
    import app.main as main_module

    (tmp_path / "go").mkdir()
    (tmp_path / "go" / "go.owl").write_text("<rdf:RDF/>")
    monkeypatch.setattr(main_module, "ONTOLOGIES_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(corpus):
    import app.main as main_module
    from httpx import ASGITransport, AsyncClient

    main_module.redis_client = FakeRedis([
        {"ontology": "go", "ontology_id": "go", "title": "Gene Ontology",
         "description": "The Gene Ontology.", "home_page": "http://geneontology.org",
         "version_info": "2026-03-25", "license": "CC BY 4.0",
         "url": "http://go-worker:80", "status": "online",
         "reasoner_status": "classified", "class_count": 47000},
        {"ontology": "sparse", "ontology_id": "sparse", "title": "",
         "url": "http://x:80", "status": "online"},
    ])
    main_module.es_mgr = MagicMock()
    return AsyncClient(transport=ASGITransport(app=main_module.app),
                       base_url="http://test")


@pytest.mark.unit
class TestReportedFieldsArePresent:

    @pytest.mark.asyncio
    async def test_every_field_from_the_report(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "go")
        missing = [f for f in REPORTED_FIELDS if f not in entry]
        assert not missing, f"still missing: {missing}"

    @pytest.mark.asyncio
    async def test_the_values_are_real(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "go")
        assert entry["name"] == "Gene Ontology"
        assert entry["homepage"] == "http://geneontology.org"
        assert entry["description"] == "The Gene Ontology."
        assert entry["version"] == "2026-03-25"
        assert entry["download_url"] == "media/ontologies/GO/1/go.owl"
        assert entry["class_count"] == 47000

    @pytest.mark.asyncio
    async def test_the_reasoner_outcome_is_included(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "go")
        assert entry["reasoner_status"] == "Classified"


@pytest.mark.unit
class TestExistingCallersAreUnaffected:

    @pytest.mark.asyncio
    async def test_id_title_status_keep_their_meaning(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "go")
        assert entry["id"] == "go"
        assert entry["title"] == "Gene Ontology"
        assert entry["status"] == "online", "status is still the serving state"

    @pytest.mark.asyncio
    async def test_the_envelope_is_unchanged(self, client):
        body = (await client.get("/api/listOntologies")).json()
        assert set(body) == {"result"}
        assert isinstance(body["result"], list)


@pytest.mark.unit
class TestAbsentDataIsNull:
    """Null, not "", so a harvester can tell "we do not have this" from
    "we have it and it is empty"."""

    @pytest.mark.asyncio
    async def test_missing_fields_are_null(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "sparse")
        for f in ("description", "homepage", "version", "license"):
            assert entry[f] is None, f"{f} should be null, got {entry[f]!r}"

    @pytest.mark.asyncio
    async def test_download_url_is_null_when_no_file_is_held(self, client):
        entry = next(e for e in (await client.get("/api/listOntologies")).json()["result"]
                     if e["id"] == "sparse")
        assert entry["download_url"] is None
