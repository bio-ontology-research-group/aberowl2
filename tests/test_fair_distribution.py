"""The distribution record must advertise the file the service serves.

The service serves ontology files at /media/ontologies/{acronym}/1/{file} and
the v1 API returns a download_url for all 971 registered ontologies, but the
MOD/DCAT/Hydra catalogue emitted only dcat:accessURL, pointing at the upstream
project homepage. A consumer harvesting /artefacts therefore found a
distribution that named a media type and gave no way to retrieve it, and had
to fall back to a non-catalogue endpoint. Issue #118.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


class _Url:
    scheme = "https"
    netloc = "aber-owl.net"


class _Request:
    url = _Url()


@pytest.fixture
def fields():
    from app.main import _distribution_file_fields
    return _distribution_file_fields


def test_no_fields_when_no_file_is_held(fields, tmp_path, monkeypatch):
    """A path we cannot serve must never appear in someone else's records."""
    import app.api_v1 as v1
    monkeypatch.setattr(v1, "_ontologies_dir", lambda: tmp_path)
    assert fields(_Request(), "nosuchontology") == {}


def test_download_url_is_absolute_and_typed(fields, tmp_path, monkeypatch):
    import app.api_v1 as v1
    monkeypatch.setattr(v1, "_ontologies_dir", lambda: tmp_path)
    (tmp_path / "go").mkdir()
    (tmp_path / "go" / "go.owl").write_bytes(b"<rdf:RDF/>")

    out = fields(_Request(), "go")
    assert out["dcat:downloadURL"] == {
        "@id": "https://aber-owl.net/media/ontologies/GO/1/go.owl",
        "@type": "rdfs:Resource",
    }
    assert out["dcat:mediaType"] == "application/rdf+xml"


def test_byte_size_reports_the_file_on_disk(fields, tmp_path, monkeypatch):
    import app.api_v1 as v1
    monkeypatch.setattr(v1, "_ontologies_dir", lambda: tmp_path)
    (tmp_path / "cl").mkdir()
    (tmp_path / "cl" / "cl.owl").write_bytes(b"x" * 4096)

    out = fields(_Request(), "cl")
    assert out["dcat:byteSize"] == {
        "@type": "xsd:nonNegativeInteger",
        "@value": 4096,
    }


def test_acronym_case_does_not_matter(fields, tmp_path, monkeypatch):
    """Registry ids are lowercase; the served path uppercases the acronym."""
    import app.api_v1 as v1
    monkeypatch.setattr(v1, "_ontologies_dir", lambda: tmp_path)
    (tmp_path / "chebi").mkdir()
    (tmp_path / "chebi" / "chebi.owl").write_bytes(b"<rdf:RDF/>")

    out = fields(_Request(), "CHEBI")
    assert out["dcat:downloadURL"]["@id"].endswith("/media/ontologies/CHEBI/1/chebi.owl")
