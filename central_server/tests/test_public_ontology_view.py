"""Security regression tests for registry-entry serialization.

`_public_ontology_view` is the allow-list filter every client-facing handler
must run registry entries through. The public REST API previously leaked the
worker `secret_key` (which authorizes the workers' mutating endpoints) and the
internal worker `url` / `server_url` from `GET /api/getOntology`. These tests
lock in that those fields can never be serialized to clients.

Run: `python -m pytest central_server/tests/test_public_ontology_view.py`
or standalone: `python central_server/tests/test_public_ontology_view.py`.
"""

import importlib.util
import os
import sys

# Import _public_ontology_view from app/main.py without importing the whole
# FastAPI app (which needs Redis/ES). We load the module source and exec only
# the helper + its allow-list, avoiding heavy import-time side effects.
_APP_MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "main.py"
)


def _load_helper():
    import ast

    with open(_APP_MAIN) as fh:
        tree = ast.parse(fh.read())
    wanted = {"_PUBLIC_ONTOLOGY_FIELDS", "_public_ontology_view"}
    ns = {"frozenset": frozenset}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) in wanted for t in node.targets
        ):
            exec(compile(ast.Module([node], []), _APP_MAIN, "exec"), ns)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module([node], []), _APP_MAIN, "exec"), ns)
    return ns["_public_ontology_view"]


_public_ontology_view = _load_helper()

# A realistic registry entry as stored in the `registered_servers` Redis hash.
_ENTRY = {
    "ontology": "GO",
    "title": "Gene Ontology",
    "description": "An ontology of gene function.",
    "version_info": "2024-01-01",
    "license": "CC BY 4.0",
    "home_page": "http://geneontology.org",
    "status": "online",
    "class_count": 50000,
    "property_count": 12,
    # --- internal fields that MUST NOT leak ---
    "secret_key": "d34db33f-super-secret",
    "url": "http://10.254.146.227:8084/",
    "server_url": "http://10.254.146.227:8084/",
    "update_status": "ok",
    "update_error": "",
}

_FORBIDDEN = ("secret_key", "url", "server_url", "update_status", "update_error")


def test_strips_all_internal_fields():
    view = _public_ontology_view(_ENTRY)
    for field in _FORBIDDEN:
        assert field not in view, f"public view leaked internal field: {field}"


def test_keeps_public_fields():
    view = _public_ontology_view(_ENTRY)
    for field in (
        "ontology", "title", "description", "version_info", "license",
        "home_page", "status", "class_count", "property_count",
    ):
        assert view.get(field) == _ENTRY[field], f"public field dropped: {field}"


def test_allow_list_blocks_new_fields_by_default():
    # A field nobody has allow-listed yet must not leak.
    entry = dict(_ENTRY, brand_new_internal_token="leak-me")
    view = _public_ontology_view(entry)
    assert "brand_new_internal_token" not in view


def test_non_dict_is_safe():
    assert _public_ontology_view(None) == {}
    assert _public_ontology_view("not a dict") == {}


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
