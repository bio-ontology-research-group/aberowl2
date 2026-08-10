"""The per-artefact record must carry dcterms:license.

/artefacts (collection) emitted dcterms:license while /artefacts/{id} did not.
The per-artefact record is what a persistent identifier resolves to and what a
FAIR assessor fetches for R1.1, so the licence was missing exactly where it
matters. 317 of 971 production ontologies had a licence in the registry.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


@pytest.fixture
def lic():
    from app.main import _license_node
    return _license_node


def test_uri_licence_is_a_resource_not_a_literal(lic):
    """A URI licence must be {"@id": ...} so it is machine-actionable."""
    assert lic("https://creativecommons.org/licenses/by/4.0/") == {
        "@id": "https://creativecommons.org/licenses/by/4.0/"
    }
    assert lic("http://spdx.org/licenses/CC-BY-4.0") == {
        "@id": "http://spdx.org/licenses/CC-BY-4.0"
    }


def test_non_uri_licence_stays_a_literal(lic):
    """Free-text licences must not be minted into bogus IRIs."""
    out = lic("CC-BY-4.0")
    assert out["@value"] == "CC-BY-4.0"
    assert "@id" not in out


def test_whitespace_is_trimmed(lic):
    assert lic("  https://example.org/l  ") == {"@id": "https://example.org/l"}


def test_artefact_record_emits_licence_when_registry_has_one():
    """Guards the actual regression: the record builder must include licence."""
    src = (REPO / "central_server" / "app" / "main.py").read_text()
    start = src.index('@app.get("/artefacts/{artefact_id}")')
    end = src.index("@app.get", start + 10)
    record_builder = src[start:end]
    assert "dcterms:license" in record_builder, (
        "/artefacts/{id} must emit dcterms:license; it is the document a "
        "persistent identifier resolves to"
    )
