"""Unit tests for the self-host init helper's pure logic (no docker, no network)."""
import importlib.util
import os

import pytest

# Load deploy/selfhost_init.py by path (deploy/ is not a package).
_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_PATH = os.path.join(_HERE, "..", "deploy", "selfhost_init.py")
_spec = importlib.util.spec_from_file_location("selfhost_init", _MOD_PATH)
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)


# ---- derive_id -----------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("go.owl", "go"),
    ("GO.owl", "go"),
    ("hp_active.owl", "hp"),
    ("mondo.owl.gz", "mondo"),
    ("/data/ontologies/CL.owl", "cl"),
    ("http://purl.obolibrary.org/obo/uberon.owl", "uberon"),
    ("chebi.obo", "chebi"),
])
def test_derive_id(filename, expected):
    assert si.derive_id(filename) == expected


def test_is_url():
    assert si.is_url("http://x/y.owl")
    assert si.is_url("https://x/y.owl")
    assert not si.is_url("/data/y.owl")
    assert not si.is_url("y.owl")


# ---- parse_sources -------------------------------------------------------

def test_parse_sources_url_only():
    specs = si.parse_sources("http://purl.obolibrary.org/obo/go.owl")
    assert specs == [{"id": "go", "url": "http://purl.obolibrary.org/obo/go.owl", "reasoner": "elk"}]


def test_parse_sources_id_and_reasoner():
    text = "hp   http://purl.obolibrary.org/obo/hp.owl\nmp http://x/mp.owl hermit"
    specs = si.parse_sources(text)
    assert specs[0] == {"id": "hp", "url": "http://purl.obolibrary.org/obo/hp.owl", "reasoner": "elk"}
    assert specs[1] == {"id": "mp", "url": "http://x/mp.owl", "reasoner": "hermit"}


def test_parse_sources_ignores_comments_and_blanks():
    text = "# a comment\n\n  \nhttp://x/go.owl  # trailing\n"
    specs = si.parse_sources(text)
    assert len(specs) == 1 and specs[0]["id"] == "go"


def test_parse_sources_rejects_line_without_url():
    with pytest.raises(ValueError):
        si.parse_sources("just some words no link")


# ---- resolve_specs -------------------------------------------------------

def test_resolve_bare_files_only():
    listing = ["pizza.owl", "bfo.owl", "README.txt", "ontologies.json"]
    specs = si.resolve_specs(listing)
    ids = sorted(s["id"] for s in specs)
    assert ids == ["bfo", "pizza"]
    assert all(s["reasoner"] == "elk" and "path" in s for s in specs)


def test_resolve_files_plus_sources():
    specs = si.resolve_specs(["pizza.owl"], sources_text="http://x/go.owl")
    kinds = {s["id"]: ("url" if "url" in s else "path") for s in specs}
    assert kinds == {"pizza": "path", "go": "url"}


def test_resolve_dedupes_source_against_file():
    # a file pizza.owl and a source that also resolves to id 'pizza' -> file wins
    specs = si.resolve_specs(["pizza.owl"], sources_text="pizza http://x/pizza.owl")
    assert len(specs) == 1 and "path" in specs[0]


def test_resolve_user_config_is_authoritative():
    listing = ["pizza.owl"]  # ignored when a user config is given
    cfg = [{"id": "GO", "url": "http://x/go.owl"}, {"path": "local/hp.owl", "reasoner": "HERMIT"}]
    specs = si.resolve_specs(listing, user_config=cfg)
    assert specs[0] == {"id": "go", "url": "http://x/go.owl", "reasoner": "elk"}
    assert specs[1] == {"id": "hp", "path": "local/hp.owl", "reasoner": "hermit"}


def test_resolve_user_config_requires_path_or_url():
    with pytest.raises(ValueError):
        si.resolve_specs([], user_config=[{"id": "x", "reasoner": "elk"}])


# ---- worker_config -------------------------------------------------------

def test_worker_config_uses_per_id_subdir():
    # Central hardcodes /data/{id}/{id}.owl for reindex, so worker paths must match.
    specs = [
        {"id": "pizza", "path": "pizza.owl", "reasoner": "elk"},
        {"id": "go", "url": "http://x/go.owl", "reasoner": "elk"},
    ]
    cfg = si.worker_config(specs, data_mount="/data")
    assert cfg == [
        {"id": "pizza", "path": "/data/pizza/pizza.owl", "reasoner": "elk"},
        {"id": "go", "path": "/data/go/go.owl", "reasoner": "elk"},
    ]


def test_onto_rel_path():
    assert si.onto_rel_path("go") == "go/go.owl"


# ---- extract_loaded_ids --------------------------------------------------

def test_extract_loaded_ids_object_and_string_forms():
    assert si.extract_loaded_ids({"ontologies": ["go", "hp"]}) == ["go", "hp"]
    assert si.extract_loaded_ids({"ontologies": [{"id": "go"}, {"ontology": "hp"}, {"name": "mp"}]}) == ["go", "hp", "mp"]
    assert si.extract_loaded_ids({}) == []
    assert si.extract_loaded_ids({"ontologies": [{"nope": 1}]}) == []


def test_extract_loaded_ids_worker_shape():
    # The real worker /listLoadedOntologies + /health payload uses "ontologyId".
    body = {"status": "ok", "ontologies": [
        {"ontologyId": "pizza", "status": "classified", "reasonerType": "elk", "classCount": 100}]}
    assert si.extract_loaded_ids(body) == ["pizza"]
