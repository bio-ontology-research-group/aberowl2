"""
Live browsing tests against beta.aber-owl.net.

These tests verify that the class browsing workflow works end-to-end:
  - getClass returns class details
  - DL query for subclasses/superclasses works
  - The SPA class page route serves the frontend

Run with: uv run pytest tests/test_live_browsing.py -v
"""

import pytest
import requests

BETA_URL = "https://beta.aber-owl.net"


def _get(path, params=None, timeout=30):
    return requests.get(f"{BETA_URL}{path}", params=params, timeout=timeout)


@pytest.mark.live
@pytest.mark.timeout(30)
def test_get_class_pizza_cajun():
    """getClass returns details for Pizza Cajun class."""
    r = _get("/api/getClass", params={
        "query": "http://www.co-ode.org/ontologies/pizza/pizza.owl#Cajun",
        "ontology": "pizza",
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "class" in body or "owlClass" in body, f"No class IRI in response: {body}"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_get_class_go_biological_process():
    """getClass returns details for GO biological_process class."""
    r = _get("/api/getClass", params={
        "query": "http://purl.obolibrary.org/obo/GO_0008150",
        "ontology": "go",
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "class" in body or "owlClass" in body, f"No class IRI in response: {body}"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_browse_pizza_subclasses():
    """DL query for Pizza subclasses works (used by hierarchy tree)."""
    r = _get("/api/dlquery_all", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
        "type": "subclass",
        "ontologies": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["result"]) > 0, "No subclasses returned"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_browse_pizza_superclasses():
    """DL query for Cajun superclasses works."""
    r = _get("/api/dlquery_all", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Cajun>",
        "type": "superclass",
        "ontologies": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["result"]) > 0, "No superclasses returned"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_spa_class_page_serves_html():
    """The SPA class page route returns the frontend HTML."""
    r = _get("/ontology/pizza/class/http%3A%2F%2Fwww.co-ode.org%2Fontologies%2Fpizza%2Fpizza.owl%23Cajun")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower(), "Not serving SPA HTML"


@pytest.mark.live
@pytest.mark.timeout(30)
def test_get_class_not_found():
    """getClass returns 404 for nonexistent class."""
    r = _get("/api/getClass", params={
        "query": "http://example.org/nonexistent_class_xyz",
        "ontology": "pizza",
    })
    assert r.status_code == 404
