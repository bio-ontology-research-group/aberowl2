"""
Integration tests for Groovy servlet endpoints.

These tests require a running pizza ontology stack (Docker).
They verify every servlet endpoint with the new ontologyId parameter.

Run with: pytest tests/test_servlet_integration.py -v -m slow
"""

import json
import shutil
import time
from pathlib import Path

import pytest
import requests

from tests.conftest import (
    ONT_HOST_PATH,
    PORT_ES,
    TEST_SECRET_KEY,
)


def _get(url, params=None, timeout=30):
    r = requests.get(url, params=params, timeout=timeout)
    return r


def _post(url, json_body=None, timeout=30):
    r = requests.post(url, json=json_body, timeout=timeout)
    return r


def _poll_task(api_url, task_id, timeout=300, interval=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{api_url}/updateStatus.groovy", params={"taskId": task_id}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") != "pending":
                return data
        time.sleep(interval)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout} s")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_health_endpoint(pizza_stack):
    """Health endpoint returns status and loaded ontology list."""
    r = _get(f"{pizza_stack}/health.groovy")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "loading")
    assert "totalLoaded" in body
    assert body["totalLoaded"] >= 1
    assert "ontologies" in body
    assert len(body["ontologies"]) >= 1


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_health_root_endpoint(pizza_stack):
    """Root health endpoint also works."""
    base_url = pizza_stack.replace("/api", "")
    r = _get(f"{base_url}/health.groovy")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# runQuery
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_run_query_with_ontology_id(pizza_stack):
    """runQuery with explicit ontologyId parameter."""
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
        "type": "subclass",
        "direct": "true",
        "labels": "true",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    assert len(body["result"]) > 0
    for entry in body["result"]:
        assert "class" in entry
        assert "label" in entry
        assert entry["ontology"] == "pizza"


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_run_query_auto_ontology_id(pizza_stack):
    """runQuery auto-resolves ontologyId when only one ontology is loaded."""
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
        "type": "subclass",
        "direct": "true",
        "labels": "true",
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["result"]) > 0


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_run_query_invalid_ontology_id(pizza_stack):
    """runQuery returns 404 for unknown ontologyId."""
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "cell",
        "type": "subclass",
        "ontologyId": "nonexistent_ontology_xyz",
    })
    assert r.status_code == 404


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_run_query_bad_syntax(pizza_stack):
    """runQuery returns 400 for invalid Manchester OWL syntax."""
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "this is not valid manchester syntax <<<>>>",
        "type": "subclass",
        "ontologyId": "pizza",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# findRoot
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_find_root(pizza_stack):
    """findRoot returns a hierarchy for a known class."""
    r = _get(f"{pizza_stack}/findRoot.groovy", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert "result" in body


# ---------------------------------------------------------------------------
# getObjectProperties
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_get_object_properties(pizza_stack):
    """getObjectProperties returns property list."""
    r = _get(f"{pizza_stack}/getObjectProperties.groovy", params={
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert "result" in body


# ---------------------------------------------------------------------------
# getStatistics
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_get_statistics(pizza_stack):
    """getStatistics returns comprehensive ontology stats."""
    r = _get(f"{pizza_stack}/getStatistics.groovy", params={
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["class_count"] > 0
    assert "property_count" in body
    assert "axiom_count" in body
    assert body["ontology_id"] == "pizza"
    assert body["reasoner_type"] == "elk"
    assert body["status"] in ("classified", "incoherent")


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_get_statistics_all(pizza_stack):
    """getStatistics without ontologyId returns summary of all loaded ontologies."""
    r = _get(f"{pizza_stack}/getStatistics.groovy")
    assert r.status_code == 200
    body = r.json()
    assert "ontologies" in body
    assert len(body["ontologies"]) >= 1


# ---------------------------------------------------------------------------
# getSparqlExamples
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_get_sparql_examples(pizza_stack):
    """getSparqlExamples returns example queries."""
    r = _get(f"{pizza_stack}/getSparqlExamples.groovy", params={
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    # Should have at least the superclass label example
    assert "exampleSuperclassLabel" in body


# ---------------------------------------------------------------------------
# listLoadedOntologies
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_list_loaded_ontologies(pizza_stack):
    """listLoadedOntologies returns loaded ontology list."""
    r = _get(f"{pizza_stack}/listLoadedOntologies.groovy")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert len(body["ontologies"]) >= 1
    ont = body["ontologies"][0]
    assert "ontologyId" in ont
    assert "status" in ont
    assert "reasonerType" in ont
    assert "classCount" in ont


# ---------------------------------------------------------------------------
# validateOntology
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_validate_ontology(pizza_stack):
    """validateOntology validates an OWL file."""
    r = _get(f"{pizza_stack}/validateOntology.groovy", params={
        "owlPath": "/data/pizza_active.owl",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["classCount"] > 0


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_validate_ontology_bad_path(pizza_stack):
    """validateOntology rejects paths outside /data/."""
    r = _get(f"{pizza_stack}/validateOntology.groovy", params={
        "owlPath": "/etc/passwd",
    })
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# updateOntology with ontologyId
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(300)
def test_update_ontology_with_ontology_id(pizza_stack):
    """updateOntology hot-swaps using new ontologyId parameter."""
    ont_dir = ONT_HOST_PATH / "pizza"
    staging = ont_dir / "pizza_staging2.owl"
    shutil.copy2(ont_dir / "pizza_active.owl", staging)

    r = _post(f"{pizza_stack}/updateOntology.groovy", json_body={
        "owlPath": "/data/pizza_staging2.owl",
        "ontologyId": "pizza",
        "reasonerType": "elk",
        "secretKey": TEST_SECRET_KEY,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    assert body["ontologyId"] == "pizza"

    result = _poll_task(pizza_stack, body["taskId"], timeout=240)
    assert result["status"] == "success"


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_update_ontology_unauthorized(pizza_stack):
    """updateOntology rejects bad secret key."""
    r = _post(f"{pizza_stack}/updateOntology.groovy", json_body={
        "owlPath": "/data/pizza_active.owl",
        "ontologyId": "pizza",
        "secretKey": "wrong_key",
    })
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# addOntology / removeOntology
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_add_ontology_unauthorized(pizza_stack):
    """addOntology rejects bad secret key."""
    r = _post(f"{pizza_stack}/addOntology.groovy", json_body={
        "ontologyId": "test_add",
        "owlPath": "/data/pizza_active.owl",
        "secretKey": "wrong_key",
    })
    assert r.status_code == 401


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_remove_ontology_unauthorized(pizza_stack):
    """removeOntology rejects bad secret key."""
    r = _post(f"{pizza_stack}/removeOntology.groovy", json_body={
        "ontologyId": "pizza",
        "secretKey": "wrong_key",
    })
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# reloadOntology (fixed memory leak)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(300)
def test_reload_ontology(pizza_stack):
    """reloadOntology reloads without memory leak."""
    r = _get(f"{pizza_stack}/reloadOntology.groovy", params={
        "ontologyId": "pizza",
        "ontologyIRI": "/data/pizza_active.owl",
        "reasonerType": "elk",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["classCount"] > 0

    # Verify the ontology still works after reload
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
        "type": "subclass",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    assert len(r.json()["result"]) > 0
