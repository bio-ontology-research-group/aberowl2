"""Tests for the FAIR artefact date helpers.

`dcterms:issued` / `dcterms:modified` in the `/artefacts` records must come from
real registry metadata, never a request-time `datetime.utcnow()` stamp (the
earlier bug: every record showed the moment it was queried). These tests lock in
that the helpers surface real dates and omit a field when no real date exists.

The helpers are extracted from `app/main.py` without importing the FastAPI app
(which needs Redis/ES), following test_public_ontology_view.py.

Run: `python -m pytest central_server/tests/test_fair_dates.py`
"""
import ast
import os
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

_APP_MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "main.py"
)


def _load(names):
    with open(_APP_MAIN) as fh:
        tree = ast.parse(fh.read())
    ns = {"datetime": datetime, "Optional": Optional, "Dict": Dict, "Any": Any}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), _APP_MAIN, "exec"), ns)
    return ns


_ns = _load({"_norm_date", "_artefact_dates", "_date_fields"})
_norm_date = _ns["_norm_date"]
_artefact_dates = _ns["_artefact_dates"]
_date_fields = _ns["_date_fields"]


# ---- _norm_date ----------------------------------------------------------

def test_norm_date_iso():
    assert _norm_date("2024-11-15") == "2024-11-15"
    assert _norm_date("2024-11-15T09:12:00+00:00").startswith("2024-11-15T09:12:00")


def test_norm_date_http_last_modified():
    # HTTP Last-Modified header form.
    assert _norm_date("Mon, 03 Jun 2024 09:12:00 GMT").startswith("2024-06-03T09:12:00")


def test_norm_date_rejects_garbage_and_empty():
    for bad in (None, "", "  ", "version 2", "2.0", 12345):
        assert _norm_date(bad) is None


# ---- _artefact_dates -----------------------------------------------------

def test_artefact_dates_real_values():
    entry = {"version_info": "2024-11-15",
             "source_last_modified": "Mon, 03 Jun 2024 09:12:00 GMT"}
    issued, modified = _artefact_dates(entry)
    assert issued == "2024-11-15"
    assert modified.startswith("2024-06-03T09:12:00")


def test_artefact_dates_modified_falls_back_to_indexed():
    entry = {"last_indexed": "2025-02-01T00:00:00+00:00"}
    issued, modified = _artefact_dates(entry)
    assert issued is None                       # no version date -> omitted
    assert modified.startswith("2025-02-01")


def test_artefact_dates_none_when_absent():
    # A non-date version and no modified fields -> nothing real to report.
    assert _artefact_dates({"version_info": "2.0"}) == (None, None)
    assert _artefact_dates({}) == (None, None)


# ---- _date_fields --------------------------------------------------------

def test_date_fields_never_fabricates_now():
    """The regression: a record with no real dates must carry NO date fields
    (previously it stamped datetime.utcnow())."""
    assert _date_fields({"version_info": "2.0"}) == {}
    assert _date_fields({}) == {}


def test_date_fields_types_and_values():
    fields = _date_fields({"version_info": "2024-11-15",
                           "source_last_modified": "Mon, 03 Jun 2024 09:12:00 GMT"})
    assert fields["dcterms:issued"] == {"@type": "xsd:date", "@value": "2024-11-15"}
    assert fields["dcterms:modified"]["@type"] == "xsd:dateTime"
    assert fields["dcterms:modified"]["@value"].startswith("2024-06-03T09:12:00")
