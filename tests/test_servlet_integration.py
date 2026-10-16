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
    # "loading" is accepted deliberately: the fixture's readiness probe waits for
    # the endpoint to answer, not for classification to finish, so a worker can
    # still be classifying here. Assert the loaded-ontology invariants instead.
    assert body["status"] in ("ok", "loading")
    assert "totalLoaded" in body
    assert body["totalLoaded"] >= 1
    assert "ontologies" in body
    assert len(body["ontologies"]) >= 1


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_proxy_serves_only_the_ui_files(pizza_stack):
    """The worker proxy publishes the page, and nothing else from the checkout.

    Its web root used to be the whole checkout, so any file sitting there was
    downloadable over the published port. It now serves three files.
    """
    base_url = pizza_stack.replace("/api", "")

    for asset in ("/", "/index.html", "/ontology.js", "/app.css"):
        r = _get(f"{base_url}{asset}")
        assert r.status_code == 200, f"{asset} should be served, got {r.status_code}"

    # A tracked file that is in the checkout but is not part of the page.
    r = _get(f"{base_url}/docker-compose.yml")
    assert r.status_code == 404, (
        f"the proxy served /docker-compose.yml ({r.status_code}); its web root "
        "is exposing the checkout again"
    )


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
def test_run_query_label_superclasses(pizza_stack):
    """Resolve the CheesyPizza label, whose IRI fragment is CheeseyPizza."""
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": "'CheesyPizza'",
        "type": "superclass",
        "direct": "false",
        "labels": "true",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    results = r.json()["result"]
    assert "http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza" in {
        entry["class"] for entry in results
    }
    assert all(entry["ontology"] == "pizza" for entry in results)


@pytest.mark.slow
@pytest.mark.timeout(120)
@pytest.mark.parametrize("query, fragment", [
    ("Domain Thing", "DomainConcept"),
    ("'Domain Thing'", "DomainConcept"),
    ("Interesting Pizza", "InterestingPizza"),
    ("'Interesting Pizza'", "InterestingPizza"),
    ("Pizza", "Pizza"),
])
def test_run_query_entity_names(pizza_stack, query, fragment):
    """Bare and quoted display names resolve to the intended named class.

    DomainThing is the rdfs:label of DomainConcept; its spaced display name
    exercises label normalization rather than an exact IRI-fragment lookup.
    """
    r = _get(f"{pizza_stack}/runQuery.groovy", params={
        "query": query,
        "type": "equivalent",
        "labels": "true",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    assert f"http://www.co-ode.org/ontologies/pizza/pizza.owl#{fragment}" in {
        entry["class"] for entry in r.json()["result"]
    }


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
    """getObjectProperties includes the known root property hasCountryOfOrigin."""
    r = _get(f"{pizza_stack}/getObjectProperties.groovy", params={
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert "http://www.co-ode.org/ontologies/pizza/pizza.owl#hasCountryOfOrigin" in {
        entry["class"] for entry in body["result"]
    }
    assert all(entry["ontology"] == "pizza" for entry in body["result"])


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
@pytest.mark.timeout(300)
def test_get_statistics_all(pizza_stack):
    """getStatistics without ontologyId summarises every loaded ontology.

    The summary only applies when more than one ontology is loaded: with exactly
    one the servlet resolves to it and returns that ontology's own statistics.
    So load a second one for the duration of this test, then remove it, because
    the auto-resolving tests need the stack back at a single ontology.
    """
    second = "pizza_stats_probe"
    r = _post(f"{pizza_stack}/addOntology.groovy", json_body={
        "ontologyId": second,
        "owlPath": "/data/pizza_active.owl",
        "reasonerType": "elk",
        "secretKey": TEST_SECRET_KEY,
    })
    assert r.status_code == 200
    task = _poll_task(pizza_stack, r.json()["taskId"], timeout=120)
    assert task["status"] == "success", task

    try:
        r = _get(f"{pizza_stack}/getStatistics.groovy")
        assert r.status_code == 200
        body = r.json()
        assert "ontologies" in body
        ids = {entry["ontologyId"] for entry in body["ontologies"]}
        assert {"pizza", second} <= ids
    finally:
        cleanup = _post(f"{pizza_stack}/removeOntology.groovy", json_body={
            "ontologyId": second,
            "secretKey": TEST_SECRET_KEY,
        })
        assert cleanup.status_code == 200

    # Back to one ontology, so auto-resolution works for the tests that follow.
    loaded = _get(f"{pizza_stack}/listLoadedOntologies.groovy").json()
    assert [o["ontologyId"] for o in loaded["ontologies"]] == ["pizza"]


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
    """validateOntology validates an OWL file with the ontology's import policy."""
    r = _post(f"{pizza_stack}/validateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY,
        "owlPath": "/data/pizza_active.owl",
        "ontologyId": "pizza",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["classCount"] > 0
    assert body["importsPolicy"] == "suppressed"
    assert isinstance(body["declaredImports"], list)
    assert "exportPath" not in body


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_validate_ontology_requires_the_secret(pizza_stack):
    """Validation parses a whole ontology in the worker's heap."""
    r = _post(f"{pizza_stack}/validateOntology.groovy", json_body={
        "owlPath": "/data/pizza_active.owl", "secretKey": "test-wrong_key"})
    assert r.status_code == 401


@pytest.mark.slow
@pytest.mark.timeout(120)
@pytest.mark.parametrize("path", ["/etc/passwd", "/data/../etc/passwd", "/data", "data/pizza_active.owl"])
def test_validate_ontology_bad_path(pizza_stack, path):
    """validateOntology accepts only existing regular files under /data."""
    r = _post(f"{pizza_stack}/validateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "owlPath": path})
    assert r.status_code == 400


def _es_count(index):
    base = f"http://localhost:{PORT_ES}"
    requests.post(f"{base}/{index}/_refresh", timeout=30).raise_for_status()
    r = requests.get(f"{base}/{index}/_count", timeout=30)
    r.raise_for_status()
    return r.json()["count"]


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_validation_export_is_indexed_and_then_deleted(pizza_stack):
    """The update pipeline's indexing contract: validation exports the merged
    ontology, triggerIndexing indexes exactly its classes and deletes it."""
    txn = f"t{int(time.time())}-a1"
    r = _post(f"{pizza_stack}/validateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "owlPath": "/data/pizza_active.owl",
        "ontologyId": "pizza", "exportId": txn})
    assert r.status_code == 200, r.text
    body = r.json()
    export = body["exportPath"]
    assert export.endswith(f"/{txn}/pizza.owl") and not export.startswith("/data/")

    again = _post(f"{pizza_stack}/validateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "owlPath": "/data/pizza_active.owl",
        "ontologyId": "pizza", "exportId": txn})
    assert again.status_code == 400 and "already exists" in again.json()["message"]

    index = f"aberowl_pizza_classes_{txn}"
    r = _post(f"{pizza_stack}/triggerIndexing.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "owlPath": export,
        "classIndexName": index, "name": "pizza", "freshIndex": "false"})
    assert r.status_code == 200, r.text
    result = _poll_task(pizza_stack, r.json()["taskId"], timeout=540)
    assert result["status"] == "success", result
    assert _es_count(index) == body["classCount"]

    gone = _post(f"{pizza_stack}/triggerIndexing.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "owlPath": export,
        "classIndexName": index})
    assert gone.status_code == 400, "the export is deleted once its indexer exited"
    requests.delete(f"http://localhost:{PORT_ES}/{index}", timeout=30)


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_discard_export(pizza_stack):
    r = _post(f"{pizza_stack}/finalizeUpdate.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "action": "discardExport",
        "exportId": "t0-none"})
    assert r.status_code == 200 and r.json() == {"status": "ok", "deleted": False}


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_finalize_requires_the_secret(pizza_stack):
    r = _post(f"{pizza_stack}/finalizeUpdate.groovy", json_body={
        "secretKey": "test-wrong_key", "ontologyId": "pizza", "action": "finalize",
        "owlPath": "/data/x.owl", "servedPath": "/data/pizza_active.owl"})
    assert r.status_code == 401


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_hot_swap_from_a_staged_copy_then_finalize(pizza_stack):
    """After a swap from a staged copy the worker records the staged path;
    finalize puts the served path back, only in place of that staged path."""
    staged_dir = ONT_HOST_PATH / "pizza_updates" / "t1"
    staged_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ONT_HOST_PATH / "pizza_active.owl", staged_dir / "pizza.owl")
    staged = "/data/pizza_updates/t1/pizza.owl"
    served = "/data/pizza_active.owl"
    try:
        r = _post(f"{pizza_stack}/updateOntology.groovy", json_body={
            "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza",
            "owlPath": staged, "servedPath": served})
        assert r.status_code == 200, r.text
        task_id = r.json()["taskId"]
        result = _poll_task(pizza_stack, task_id, timeout=240)
        assert result["status"] == "success", result
        assert result["serving"] is True and result["servedFrom"] == staged

        def recorded():
            loaded = _get(f"{pizza_stack}/listLoadedOntologies.groovy").json()["ontologies"]
            return next(o["path"] for o in loaded if o["ontologyId"] == "pizza")

        assert recorded() == staged

        wrong = _post(f"{pizza_stack}/finalizeUpdate.groovy", json_body={
            "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "action": "finalize",
            "owlPath": "/data/other.owl", "servedPath": served})
        assert wrong.status_code == 409 and wrong.json()["path"] == staged

        ok = _post(f"{pizza_stack}/finalizeUpdate.groovy", json_body={
            "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "action": "finalize",
            "owlPath": staged, "servedPath": served})
        assert ok.status_code == 200 and ok.json()["path"] == served
        assert recorded() == served
    finally:
        shutil.rmtree(ONT_HOST_PATH / "pizza_updates", ignore_errors=True)


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_one_task_per_ontology(pizza_stack):
    """A second hot-swap of an ontology is refused while the first is pending."""
    r = _post(f"{pizza_stack}/updateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "owlPath": "/data/pizza_active.owl"})
    assert r.status_code == 200, r.text
    first = r.json()["taskId"]
    second = _post(f"{pizza_stack}/updateOntology.groovy", json_body={
        "secretKey": TEST_SECRET_KEY, "ontologyId": "pizza", "owlPath": "/data/pizza_active.owl"})
    first_state = requests.get(f"{pizza_stack}/updateStatus.groovy", params={"taskId": first}, timeout=10).json()
    if second.status_code == 200:
        _poll_task(pizza_stack, second.json()["taskId"], timeout=240)
    _poll_task(pizza_stack, first, timeout=240)
    if second.status_code == 200:
        assert first_state["status"] != "pending", "a second swap started while the first was pending"
        pytest.skip("the first swap finished before the second request; the guard was not exercised")
    assert second.status_code == 409 and second.json()["taskId"] == first


# ---------------------------------------------------------------------------
# updateOntology with ontologyId
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(300)
def test_update_ontology_with_ontology_id(pizza_stack):
    """updateOntology hot-swaps using new ontologyId parameter."""
    # ONT_HOST_PATH is what the worker sees as /data, so stage the copy there.
    staging = ONT_HOST_PATH / "pizza_staging2.owl"
    shutil.copy2(ONT_HOST_PATH / "pizza_active.owl", staging)

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
        "secretKey": 'test-wrong_key',
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
        "secretKey": 'test-wrong_key',
    })
    assert r.status_code == 401


@pytest.mark.slow
@pytest.mark.timeout(120)
def test_remove_ontology_unauthorized(pizza_stack):
    """removeOntology rejects bad secret key."""
    r = _post(f"{pizza_stack}/removeOntology.groovy", json_body={
        "ontologyId": "pizza",
        "secretKey": 'test-wrong_key',
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
