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

    monkeypatch.setattr(main_module, "ONTOLOGIES_DIR", str(tmp_path))
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
    """Both path components come straight from the URL.

    Two properties, and the first is the one that matters: the planted file
    outside the corpus is never returned. The second is that nothing under
    /media/ answers 200 with an HTML page — a client asking for a file download
    should get a 404 it can detect, not the web app.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filename", ["../secret.txt", "..%2Fsecret.txt", "a/b.owl"])
    async def test_filename_cannot_escape(self, client, filename):
        r = await client.get(f"/media/ontologies/GO/1/{filename}")
        assert "do not serve me" not in r.text, f"{filename!r} leaked the file"
        assert not r.headers["content-type"].startswith("application/rdf+xml")
        assert r.status_code in (400, 404), f"{filename!r} answered {r.status_code}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("acronym", ["..", "../..", "GO/.."])
    async def test_acronym_cannot_escape(self, client, acronym):
        """The property that holds regardless of where the path lands.

        An HTTP client normalises `..` before sending, so these do not all
        arrive as /media/ paths — `../..` collapses to /1/go.owl, which is not
        a media path at all and is legitimately served by the SPA. What must be
        true in every case is that the file planted outside the corpus is never
        returned.
        """
        r = await client.get(f"/media/ontologies/{acronym}/1/go.owl")
        assert "do not serve me" not in r.text
        assert not r.headers["content-type"].startswith("application/rdf+xml")

    @pytest.mark.asyncio
    async def test_an_escaping_path_that_stays_under_media_is_404(self, client):
        """Where the request does still reach /media/, it must not be the web page."""
        # Normalises to /media/ontologies/secret.txt — still a media path, so
        # the media handler owns it rather than the SPA catch-all.
        r = await client.get("/media/ontologies/GO/../secret.txt")
        assert r.status_code == 404
        assert "do not serve me" not in r.text

    @pytest.mark.asyncio
    async def test_media_never_answers_with_the_web_page(self, client):
        """The SPA catch-all would otherwise swallow these with HTTP 200."""
        r = await client.get("/media/ontologies/GO/1/../secret.txt")
        assert "<!doctype html>" not in r.text.lower()


@pytest.mark.unit
class TestPathSettingIsTheContainerOne:
    """ONTOLOGIES_HOST_PATH is the path on the HOST; compose bind-mounts it at a
    different location inside the container. Reading the host path from in here
    finds nothing, and every download_url comes back null — which is what
    happened on the first deployment of this feature."""

    def test_serving_uses_the_container_path(self, monkeypatch, tmp_path):
        import app.main as main_module
        from app.api_v1 import _ontologies_dir

        monkeypatch.setattr(main_module, "ONTOLOGIES_DIR", str(tmp_path))
        monkeypatch.setattr(main_module, "ONTOLOGIES_BASE_PATH", "/host/path/that/is/not/mounted")
        assert _ontologies_dir() == tmp_path

    def test_the_two_settings_are_independent(self):
        import app.main as main_module

        assert hasattr(main_module, "ONTOLOGIES_DIR")
        assert hasattr(main_module, "ONTOLOGIES_BASE_PATH")
