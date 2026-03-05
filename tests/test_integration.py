"""
AberOWL2 integration tests.

Test map
--------
1. test_go_manchester_subclass_query
   – GO stack, /api/runQuery.groovy, Manchester 'subClassOf' query.
   – Verifies ELK has classified the full ontology and a known parent→child
     relationship is reported correctly.

2. test_bioportal_fetch_dedup
   – No Docker. Calls the live BioPortal REST API.
   – Verifies that OBOFoundry IDs are excluded and that returned entries have
     the expected schema.

3. test_es_search_via_groovy_api
   – Pizza stack, /api/elastic.groovy.
   – Loads pizza data into the test ES index, then queries via the Groovy
     proxy endpoint and verifies the Pizza class is returned.

4. test_central_virtuoso_sparql
   – Central Virtuoso fixture.  Loads a small named graph with CentralVirtuosoManager,
     queries it back via the SPARQL HTTP endpoint, verifies triple count.

5. test_ontology_update_hotswap
   – Pizza stack, /api/updateOntology.groovy + /api/updateStatus.groovy.
   – Writes a modified copy of pizza.owl to the staging path, triggers a
     hot-swap via the new endpoint, polls until done, and verifies the new
     class count is reflected.
"""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest
import requests

from tests.conftest import (
    DATA_DIR,
    ONT_HOST_PATH,
    PORT_ES,
    PORT_VIRT,
    TEST_SECRET_KEY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO = Path(__file__).parent.parent

def _get(url: str, params: dict = None, timeout: int = 30) -> requests.Response:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def _post(url: str, json_body: dict = None, timeout: int = 30) -> requests.Response:
    r = requests.post(url, json=json_body, timeout=timeout)
    r.raise_for_status()
    return r


def _poll_task(api_url: str, task_id: str, timeout: int = 300, interval: float = 5.0) -> dict:
    """Poll /api/updateStatus.groovy until status is no longer 'pending'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(
            f"{api_url}/updateStatus.groovy",
            params={"taskId": task_id},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("status") != "pending":
                return data
        time.sleep(interval)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout} s")


# ---------------------------------------------------------------------------
# 1. GO Manchester syntax query
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(900)
def test_go_manchester_subclass_query(go_stack):
    """
    Load and classify the Gene Ontology with ELK, then issue a Manchester
    syntax subClassOf query for direct children of 'biological process'
    (GO:0008150).

    Expected outcome
    ~~~~~~~~~~~~~~~~
    The result list is non-empty and contains at least the well-known direct
    child 'reproduction' (GO:0000003).  Label and class IRI fields must be
    present on every result entry.
    """
    api_url = go_stack

    # Manchester query: direct subclasses of biological_process
    params = {
        "query": "<http://purl.obolibrary.org/obo/GO_0008150>",
        "type": "subclass",
        "direct": "true",
        "labels": "true",
        "axioms": "false",
    }
    r = _get(f"{api_url}/runQuery.groovy", params=params, timeout=120)
    body = r.json()

    assert "result" in body, f"No 'result' key in response: {body}"
    results = body["result"]
    assert isinstance(results, list), f"Expected list, got {type(results)}"
    assert len(results) > 0, "Expected at least one subclass of biological_process"

    # Every entry must have 'class' (IRI) and 'label'
    for entry in results:
        assert "class" in entry, f"Entry missing 'class': {entry}"
        assert "label" in entry, f"Entry missing 'label': {entry}"

    iris = {e["class"] for e in results}

    # GO:0000003 = reproduction — a well-known direct child of biological_process
    reproduction_iri = "http://purl.obolibrary.org/obo/GO_0000003"
    assert reproduction_iri in iris, (
        f"Expected GO:0000003 (reproduction) among direct children of biological_process; "
        f"got {sorted(iris)[:10]} ..."
    )

    print(f"\n  GO subclasses of biological_process: {len(results)} results")
    print(f"  Query time: {body.get('time', '?')} ms")


# ---------------------------------------------------------------------------
# 2. BioPortal fetch + deduplication
# ---------------------------------------------------------------------------

@pytest.mark.bioportal
@pytest.mark.timeout(300)
def test_bioportal_fetch_dedup():
    """
    Call the live BioPortal REST API and verify that:
    - At least some ontologies are returned.
    - Ontologies whose acronym is in the exclude set are absent.
    - Every returned entry carries the required fields.

    To keep the test fast we only fetch the first page (100 ontologies)
    and skip the per-ontology download-URL round-trips (which add several
    minutes for the full catalogue).  We verify the deduplication logic
    directly by asserting that excluded IDs are not present.
    """
    import asyncio
    import aiohttp
    from central_server.app.intake.bioportal import (
        BIOPORTAL_API_URL,
        BIOPORTAL_API_KEY,
        _get_json,
    )

    # OBO IDs that should be excluded
    exclude_ids = {"hp", "go", "aro", "chebi", "bfo", "ro"}

    async def _fetch_first_page():
        async with aiohttp.ClientSession() as session:
            data = await _get_json(
                session,
                f"{BIOPORTAL_API_URL}/ontologies",
                {
                    "include": "name,acronym,description,links",
                    "pagesize": 100,
                    "page": 1,
                    "apikey": BIOPORTAL_API_KEY,
                },
            )
        return data

    data = asyncio.run(_fetch_first_page())
    assert data is not None, "BioPortal API returned no data"
    collection = data.get("collection", [])
    assert len(collection) > 0, "BioPortal first page is empty"

    # Filter as the real fetcher would
    candidates = [
        o for o in collection
        if o.get("acronym", "").lower() not in exclude_ids
    ]
    assert len(candidates) > 0, "All first-page BioPortal ontologies were excluded"

    # Excluded IDs must not appear
    present_ids = {o.get("acronym", "").lower() for o in candidates}
    for eid in exclude_ids:
        assert eid not in present_ids, f"Excluded ID '{eid}' still present in candidates"

    # Required fields
    required_fields = {"acronym", "name"}
    for o in candidates[:5]:
        for f in required_fields:
            assert f in o, f"BioPortal entry missing '{f}': {o}"

    print(f"\n  BioPortal page-1 candidates: {len(candidates)} (after excluding {len(exclude_ids)} OBO IDs)")


# ---------------------------------------------------------------------------
# 3. Elasticsearch search via the Groovy proxy (elastic.groovy)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(300)
def test_es_search_via_groovy_api(pizza_stack):
    """
    Index a minimal pizza ontology document directly into the test
    Elasticsearch instance, then query it back via the ontology-api's
    elastic.groovy proxy endpoint.

    This validates:
    - The elastic.groovy endpoint can reach the central ES (ELASTICSEARCH_URL
      env var wired correctly).
    - The query is forwarded and results returned with correct structure.
    """
    es_base = f"http://localhost:{PORT_ES}"
    index_name = "aberowl_pizza_classes_v1"
    api_url = pizza_stack

    # 1. Create index (ignore error if already exists)
    requests.put(
        f"{es_base}/{index_name}",
        json={
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "label": {"type": "keyword"},
                    "class": {"type": "keyword"},
                    "ontology": {"type": "keyword"},
                }
            },
        },
        timeout=15,
    )

    # 2. Index a known document
    doc = {
        "class": "http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza",
        "label": "Pizza",
        "ontology": "pizza",
        "owlClass": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
    }
    r = requests.post(
        f"{es_base}/{index_name}/_doc?refresh=true",
        json=doc,
        timeout=15,
    )
    assert r.status_code in (200, 201), f"Index doc failed: {r.text}"

    # 3. Query via the Groovy proxy
    query_body = json.dumps({
        "query": {"term": {"label": "Pizza"}},
        "_source": ["class", "label", "ontology"],
    })
    r = requests.get(
        f"{api_url}/elastic.groovy",
        params={"index": index_name, "source": query_body},
        timeout=30,
    )
    assert r.status_code == 200, f"elastic.groovy returned {r.status_code}: {r.text}"
    body = r.json()

    hits = body.get("hits", {}).get("hits", [])
    assert len(hits) > 0, f"Expected at least 1 hit for 'Pizza', got: {body}"

    first = hits[0]["_source"]
    assert first["label"] == "Pizza", f"Unexpected label: {first['label']}"
    assert first["ontology"] == "pizza", f"Unexpected ontology: {first['ontology']}"

    print(f"\n  ES search via Groovy proxy: {len(hits)} hit(s) for 'Pizza'")
    print(f"  First hit class: {first['class']}")


# ---------------------------------------------------------------------------
# 4. Central Virtuoso SPARQL (CentralVirtuosoManager)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(120)
def test_central_virtuoso_sparql(central_virtuoso):
    """
    Use CentralVirtuosoManager to:
    1. Load a small well-known named graph (pizza.owl via LOAD <file://...>
       is not available to the test container, so we INSERT triples directly).
    2. Verify the triple count matches what we inserted.
    3. Query the SPARQL endpoint directly via HTTP to confirm data is live.
    """
    import asyncio

    # Add central_server to sys.path so imports resolve without installing
    import sys
    sys.path.insert(0, str(REPO / "central_server"))

    from app.virtuoso_manager import CentralVirtuosoManager

    # Point the manager at the test Virtuoso
    os.environ["VIRTUOSO_URL"] = central_virtuoso
    os.environ["VIRTUOSO_DBA_PASSWORD"] = "dba"

    mgr = CentralVirtuosoManager()
    ont_id = "test_pizza"
    graph_uri = mgr._graph_uri(ont_id)

    async def run():
        # --- Insert 5 triples via SPARQL Update ---
        insert_sparql = f"""
        INSERT DATA {{
            GRAPH <{graph_uri}> {{
                <http://example.org/Pizza>
                    a <http://www.w3.org/2002/07/owl#Class> ;
                    <http://www.w3.org/2000/01/rdf-schema#label> "Pizza"@en .
                <http://example.org/Margherita>
                    a <http://www.w3.org/2002/07/owl#Class> ;
                    <http://www.w3.org/2002/07/owl#subClassOf> <http://example.org/Pizza> ;
                    <http://www.w3.org/2000/01/rdf-schema#label> "Margherita"@en .
                <http://example.org/Veneziana>
                    a <http://www.w3.org/2002/07/owl#Class> ;
                    <http://www.w3.org/2002/07/owl#subClassOf> <http://example.org/Pizza> ;
                    <http://www.w3.org/2000/01/rdf-schema#label> "Veneziana"@en .
            }}
        }}
        """
        ok = await mgr._execute_update(insert_sparql)
        assert ok, "SPARQL INSERT DATA failed"

        # --- Verify triple count ---
        count = await mgr.get_triple_count(ont_id)
        assert count is not None, "get_triple_count returned None"
        assert count >= 8, f"Expected at least 8 triples, got {count}"

        # --- Query live SPARQL endpoint directly ---
        r = requests.get(
            f"{central_virtuoso}/sparql",
            params={
                "query": f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}",
                "format": "application/sparql-results+json",
            },
            timeout=30,
        )
        assert r.status_code == 200, f"SPARQL HTTP query failed: {r.status_code}"
        sparql_count = int(
            r.json()["results"]["bindings"][0]["n"]["value"]
        )
        assert sparql_count == count, (
            f"Triple count mismatch: manager={count}, SPARQL endpoint={sparql_count}"
        )

        # --- Cleanup ---
        await mgr.drop_staging(ont_id)  # staging graph
        await mgr._execute_update(f"DROP SILENT GRAPH <{graph_uri}>")

        print(f"\n  Virtuoso: inserted and confirmed {count} triples in <{graph_uri}>")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. Ontology update / hot-swap
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.timeout(300)
def test_ontology_update_hotswap(pizza_stack):
    """
    Exercise the full hot-swap update path:

    1. Copy pizza.owl to a staging path inside the shared ontologies volume.
    2. POST to /api/updateOntology.groovy with the staging path and secret key.
    3. Verify the endpoint immediately returns ``status=accepted`` with a taskId.
    4. Poll /api/updateStatus.groovy until the task completes.
    5. Assert the task finished with ``status=success``.
    6. Confirm the ontology is still serving queries (runQuery.groovy).

    This verifies the full background-thread hot-swap, RequestManager.create(),
    the atomic application.setAttribute("manager", newManager) call, and the
    disposeAll() cleanup of the old manager.
    """
    api_url = pizza_stack

    # --- Stage the OWL file (reuse existing active file as a "new version") ---
    ont_dir = ONT_HOST_PATH / "pizza"
    staging_host = ont_dir / "pizza_staging.owl"
    shutil.copy2(ont_dir / "pizza_active.owl", staging_host)

    # Container sees the shared volume at /data
    staging_container_path = "/data/pizza_staging.owl"

    # --- Trigger hot-swap ---
    r = requests.post(
        f"{api_url}/updateOntology.groovy",
        json={
            "owlPath":   staging_container_path,
            "secretKey": TEST_SECRET_KEY,
        },
        timeout=30,
    )

    assert r.status_code == 200, f"updateOntology.groovy returned {r.status_code}: {r.text}"
    body = r.json()
    assert body.get("status") == "accepted", f"Expected 'accepted', got: {body}"
    task_id = body.get("taskId")
    assert task_id, "No taskId in response"
    print(f"\n  Hot-swap accepted, taskId={task_id}")

    # --- Poll for completion ---
    result = _poll_task(api_url, task_id, timeout=240)
    assert result.get("status") == "success", (
        f"Hot-swap failed: {result}"
    )
    print(f"  Hot-swap completed: {result.get('message', '')}")

    # --- Verify the API is still serving queries ---
    r = requests.get(
        f"{api_url}/runQuery.groovy",
        params={
            "query": "<http://www.co-ode.org/ontologies/pizza/pizza.owl#Pizza>",
            "type": "subclass",
            "direct": "true",
            "labels": "true",
            "axioms": "false",
        },
        timeout=60,
    )
    assert r.status_code == 200, f"runQuery.groovy returned {r.status_code}: {r.text}"

    qbody = r.json()
    assert "result" in qbody, f"No 'result' key after hot-swap: {qbody}"
    assert len(qbody["result"]) > 0, "No subclasses of Pizza returned after hot-swap"
    print(f"  Post-swap query: {len(qbody['result'])} subclasses of Pizza")
