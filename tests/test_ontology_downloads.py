"""
Serving the OWL files, and a `download_url` that resolves (issue #94, step 7).

AberOWL 1 hosted the files and put a RELATIVE path in
`submission.download_url`; consumers prefix it with the site root. Bioregistry
does precisely that, so the value has to stay relative *and* has to resolve — a
path we cannot serve becomes a broken link in their records, which is worse than
no link at all.

Hence: `download_url` is null unless the file is actually on disk.

The route also takes two hostile inputs straight from the URL, so the traversal
cases below matter more than the happy path.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


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


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A tiny ontology store: `go` has a file, `nofile` does not."""
    import app.main as main_module

    (tmp_path / "go").mkdir()
    (tmp_path / "go" / "go.owl").write_text("<rdf:RDF/>")
    (tmp_path / "nofile").mkdir()
    # A file that must never be reachable through the route.
    (tmp_path.parent / "secret.txt").write_text("do not serve me")

    monkeypatch.setattr(main_module, "ONTOLOGIES_BASE_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(corpus):
    import app.main as main_module
    from httpx import ASGITransport, AsyncClient

    main_module.redis_client = FakeRedis([
        {"ontology": "go", "ontology_id": "go", "title": "Gene Ontology",
         "url": "http://go-worker:80", "status": "online", "class_count": 47000},
        {"ontology": "nofile", "ontology_id": "nofile", "title": "No file here",
         "url": "http://x:80", "status": "online"},
    ])
    main_module.es_mgr = MagicMock()
    return AsyncClient(transport=ASGITransport(app=main_module.app),
                       base_url="http://test")


@pytest.mark.unit
class TestDownloadUrlIsHonest:

    @pytest.mark.asyncio
    async def test_present_file_gets_a_relative_v1_path(self, client):
        records = {e["acronym"]: e for e in (await client.get("/api/ontology/")).json()}
        url = records["GO"]["submission"]["download_url"]
        assert url == "media/ontologies/GO/1/go.owl"
        assert not url.startswith("http"), "must stay relative; consumers prefix it"

    @pytest.mark.asyncio
    async def test_absent_file_gets_null_not_a_guess(self, client):
        records = {e["acronym"]: e for e in (await client.get("/api/ontology/")).json()}
        assert records["NOFILE"]["submission"]["download_url"] is None

    @pytest.mark.asyncio
    async def test_the_advertised_path_actually_resolves(self, client):
        """The property that matters: what we advertise can be fetched."""
        records = {e["acronym"]: e for e in (await client.get("/api/ontology/")).json()}
        url = records["GO"]["submission"]["download_url"]
        r = await client.get("/" + url)
        assert r.status_code == 200
        assert r.text == "<rdf:RDF/>"


@pytest.mark.unit
class TestDownloadRoute:

    @pytest.mark.asyncio
    async def test_serves_the_file(self, client):
        r = await client.get("/media/ontologies/GO/1/go.owl")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/rdf+xml")

    @pytest.mark.asyncio
    async def test_any_submission_segment_resolves(self, client):
        """Archived v1 URLs carry real submission numbers; they should still work."""
        r = await client.get("/media/ontologies/GO/139/go.owl")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_lowercase_acronym_works_too(self, client):
        r = await client.get("/media/ontologies/go/1/go.owl")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_file_is_404_not_html(self, client):
        r = await client.get("/media/ontologies/NOFILE/1/nofile.owl")
        assert r.status_code == 404
        assert r.json()["status"] == "error"

    @pytest.mark.asyncio
    async def test_unknown_ontology_is_404(self, client):
        r = await client.get("/media/ontologies/NOSUCH/1/nosuch.owl")
        assert r.status_code == 404


@pytest.mark.unit
class TestTraversalIsRefused:
    """Both path components come straight from the URL."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filename", ["../secret.txt", "..%2Fsecret.txt", "a/b.owl"])
    async def test_filename_cannot_escape(self, client, filename):
        r = await client.get(f"/media/ontologies/GO/1/{filename}")
        assert r.status_code in (400, 404), f"{filename!r} was not refused"
        assert "do not serve me" not in r.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("acronym", ["..", "../..", "GO/.."])
    async def test_acronym_cannot_escape(self, client, acronym):
        r = await client.get(f"/media/ontologies/{acronym}/1/go.owl")
        assert r.status_code in (400, 404)
        assert "do not serve me" not in r.text
