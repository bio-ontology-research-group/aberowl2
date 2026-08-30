"""
The AberOWL 1 registry and search endpoints, restored (issue #94).

Two things are pinned here:

  * the response *shape* matches a real archived AberOWL 1 response, committed
    at tests/fixtures/aberowl_v1_ontology_list.json (Internet Archive snapshot,
    provenance in the fixture README) — not a shape reconstructed from memory;
  * Bioregistry's own getter logic, vendored below from
    `bioregistry/external/aberowl/__init__.py`, can parse what we return. That
    getter is the concrete consumer behind biopragmatics/bioregistry#2030.

Before this layer existed these paths fell through to the SPA catch-all and
answered HTTP 200 with `index.html`, so a client received a successful request
and parsed a web page.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

FIXTURE = REPO / "tests" / "fixtures" / "aberowl_v1_ontology_list.json"


class FakeRedis:
    def __init__(self):
        self._data = {}

    async def ping(self):
        return True

    async def hset(self, hash_name, key, value):
        self._data.setdefault(hash_name, {})[key] = value

    async def hget(self, hash_name, key):
        return self._data.get(hash_name, {}).get(key)

    async def hvals(self, hash_name):
        return list(self._data.get(hash_name, {}).values())

    async def hkeys(self, hash_name):
        return list(self._data.get(hash_name, {}).keys())

    async def close(self):
        pass


@pytest.fixture
def archived_v1():
    """A real AberOWL 1 response, as the contract to match."""
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def app_with_registry():
    import app.main as main_module

    redis = FakeRedis()
    redis._data[main_module.REGISTRY_KEY] = {
        "go": json.dumps({
            "ontology": "go",
            "ontology_id": "go",
            "title": "Gene Ontology",
            "description": "The Gene Ontology.",
            "home_page": "http://geneontology.org",
            "version_info": "2026-01-01",
            "url": "http://go-worker:80",
            "secret_key": "s3cret",
            "status": "online",
            "class_count": 47000,
            "property_count": 100,
            "individual_count": 0,
        }),
        "fma": json.dumps({
            "ontology": "fma",
            "ontology_id": "fma",
            "title": "Foundational Model of Anatomy Ontology",
            "url": "http://fma-worker:80",
            "secret_key": "s3cret",
            "status": "online",
            "class_count": 104721,
        }),
    }
    main_module.redis_client = redis
    main_module.es_mgr = MagicMock()
    return main_module


@pytest.fixture
def client(app_with_registry):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_with_registry.app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Vendored from bioregistry/external/aberowl/__init__.py — the actual consumer.
# Kept deliberately verbatim in shape so this test fails if our output drifts
# from what their getter reads.
# ---------------------------------------------------------------------------

def bioregistry_process_record(entry):
    rv = {"name": entry["name"]}
    submission = entry.get("submission", {})
    if not submission:
        return rv
    rv["homepage"] = submission.get("home_page")
    description = submission.get("description")
    if description:
        rv["description"] = description.strip().replace("\r\n", " ").replace("\n", " ")
    version = submission.get("version")
    if version:
        rv["version"] = version.strip()
    download_url_suffix = submission.get("download_url")
    if download_url_suffix and download_url_suffix.endswith(".owl"):
        rv["download_owl"] = f"http://aber-owl.net/{download_url_suffix}"
    return rv


@pytest.mark.unit
class TestV1OntologyList:

    @pytest.mark.asyncio
    async def test_returns_a_bare_list(self, client):
        """v1 used a DRF ListAPIView: a bare list, not an envelope."""
        r = await client.get("/api/ontology/", params={"format": "json"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_shape_matches_the_archived_response(self, client, archived_v1):
        """Every top-level key the real v1 emitted must be present."""
        ours = (await client.get("/api/ontology/")).json()[0]
        theirs = archived_v1[0]
        assert set(theirs) == set(ours), (
            f"missing {set(theirs) - set(ours)}, extra {set(ours) - set(theirs)}"
        )

    @pytest.mark.asyncio
    async def test_submission_shape_matches_the_archived_response(self, client, archived_v1):
        ours = (await client.get("/api/ontology/")).json()[0]["submission"]
        theirs = next(e["submission"] for e in archived_v1 if e.get("submission"))
        assert set(theirs) == set(ours), (
            f"missing {set(theirs) - set(ours)}, extra {set(ours) - set(theirs)}"
        )

    @pytest.mark.asyncio
    async def test_acronym_is_uppercase(self, client):
        """Bioregistry keys its records by acronym; v1 emitted uppercase."""
        acronyms = [e["acronym"] for e in (await client.get("/api/ontology/")).json()]
        assert "GO" in acronyms
        assert "FMA" in acronyms

    @pytest.mark.asyncio
    async def test_status_uses_v1_vocabulary(self, client):
        """v1's `status` was the reasoner outcome, never a serving state."""
        for e in (await client.get("/api/ontology/")).json():
            assert e["status"] in {"Classified", "Incoherent", "Unloadable", "Unknown"}
            assert e["status"] != "online"


@pytest.mark.unit
class TestBioregistryGetterWorks:

    @pytest.mark.asyncio
    async def test_getter_recovers_the_fields_it_needs(self, client):
        """The point of the whole exercise: their code works against ours."""
        records = (await client.get("/api/ontology/", params={"format": "json"})).json()
        go = next(e for e in records if e["acronym"] == "GO")
        parsed = bioregistry_process_record(go)
        assert parsed["name"] == "Gene Ontology"
        assert parsed["homepage"] == "http://geneontology.org"
        assert parsed["description"] == "The Gene Ontology."
        assert parsed["version"] == "2026-01-01"

    @pytest.mark.asyncio
    async def test_getter_survives_a_sparse_entry(self, client):
        """FMA has no description, homepage or version. It must not raise."""
        records = (await client.get("/api/ontology/")).json()
        fma = next(e for e in records if e["acronym"] == "FMA")
        parsed = bioregistry_process_record(fma)
        assert parsed["name"] == "Foundational Model of Anatomy Ontology"
        assert parsed.get("homepage") is None

    @pytest.mark.asyncio
    async def test_no_download_url_rather_than_a_broken_one(self, client):
        """Bioregistry prefixes download_url with the site root.

        AberOWL 2 serves no files yet, so emitting anything here would build a
        URL that 404s for their users. Null is the honest value until #95 lands.
        """
        records = (await client.get("/api/ontology/")).json()
        assert all(e["submission"]["download_url"] is None for e in records)
        assert all("download_owl" not in bioregistry_process_record(e) for e in records)


@pytest.mark.unit
class TestV1SearchEndpoints:

    @pytest.mark.asyncio
    async def test_ontology_find_requires_query(self, client):
        """v1 returned its error envelope on HTTP 200; clients branch on `status`."""
        r = await client.get("/api/ontology/_find")
        assert r.status_code == 200
        assert r.json() == {"status": "error", "message": "query field is required"}

    @pytest.mark.asyncio
    async def test_class_find_requires_query(self, client):
        r = await client.get("/api/class/_find")
        assert r.json()["status"] == "error"

    @pytest.mark.asyncio
    async def test_class_startwith_requires_ontology(self, client):
        r = await client.get("/api/class/_startwith", params={"query": "cell"})
        assert r.json() == {"status": "error", "message": "ontology is required"}

    @pytest.mark.asyncio
    async def test_class_find_returns_the_v1_envelope(self, client, app_with_registry):
        async def fake_search(term, ontology=None, prefix=False, size=100):
            return [{"owlClass": "<http://x/GO_1>", "label": ["cell"], "ontology": "go"}]

        app_with_registry.es_mgr.search_classes = fake_search
        body = (await client.get("/api/class/_find", params={"query": "cell"})).json()
        assert body["status"] == "ok"
        assert isinstance(body["result"], list)
        assert body["result"][0]["owlClass"] == "<http://x/GO_1>"

    @pytest.mark.asyncio
    async def test_ontology_find_returns_a_bare_list(self, client, app_with_registry):
        async def fake_search(term, size=50):
            return [{"ontology": "go", "name": "Gene Ontology"}]

        app_with_registry.es_mgr.search_ontologies = fake_search
        body = (await client.get("/api/ontology/_find", params={"query": "gene"})).json()
        assert isinstance(body, list)
        assert body[0]["ontology"] == "go"
