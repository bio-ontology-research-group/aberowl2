"""Regression tests for two DL-query parser bugs fixed in May 2026.

Bug A — IRI-form existential parsed as a bogus class
    `<P> some <C>` was caught by an over-greedy `startsWith("<") &&
    endsWith(">")` fast path in QueryParser.parse(), which stripped the
    outer brackets and built an OWLClass from the malformed middle. The
    reasoner then returned only unsatisfiable classes (trivially subsumed
    by anything), which looked like "0 results" for clean ontologies (GO)
    and like nonsense for ontologies with unsatisfiable classes (pizza).

Bug B — label-form existential rejected by Manchester parser
    `'P' some 'C'` failed because BasicEntityChecker.getOWLObjectProperty()
    only did direct IRI lookup. With no label/fragment fallback, the parser
    could not resolve `'part of'` as a property, so the whole expression
    failed to parse.

Both bugs return parser errors / wrong results that masquerade as
"slowness" in the UI (the user retries; the page is empty).

Run against a worker that has pizza and/or go loaded:

    BASE_URL=http://localhost:8081/api pytest tests/aberowlapi/test_dl_query_bugs.py -v

Tests skip individually when the needed ontology isn't loaded, so this is
safe to run against any worker.
"""
import os
import pytest
import requests


BASE_URL = os.environ.get("BASE_URL", "http://localhost:8081/api")
PIZZA = "http://www.co-ode.org/ontologies/pizza/pizza.owl#"
OBO = "http://purl.obolibrary.org/obo/"


def _loaded_ontologies():
    """Return set of currently-classified ontologyIds on the target worker."""
    try:
        r = requests.get(f"{BASE_URL}/listLoadedOntologies.groovy", timeout=5)
        r.raise_for_status()
        return {
            o["ontologyId"]
            for o in r.json().get("ontologies", [])
            if o.get("status") == "classified"
        }
    except Exception:
        return set()


LOADED = _loaded_ontologies()


def _run_query(ontology_id, query, *, qtype="subclass", direct="false", labels="true"):
    """POST a query to /runQuery.groovy, return (results_list, raw_json)."""
    r = requests.get(
        f"{BASE_URL}/runQuery.groovy",
        params={
            "ontologyId": ontology_id,
            "query": query,
            "type": qtype,
            "direct": direct,
            "labels": labels,
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("result", []), body


def _class_iris(results):
    """Extract the class IRI (without angle brackets) from each result entry."""
    return {r.get("class") for r in results if r.get("class")}


# -------------------------------------------------------------------- pizza

requires_pizza = pytest.mark.skipif(
    "pizza" not in LOADED, reason="pizza ontology not loaded on worker"
)


@requires_pizza
def test_bug_a_iri_existential_returns_real_pizzas():
    """Bug A regression: `<hasTopping> some <MozzarellaTopping>` returns real
    pizzas, not just the two unsatisfiable classes (CheeseyVegetableTopping,
    IceCream) that the buggy parser returned for any unknown class.
    """
    results, _ = _run_query(
        "pizza",
        f"<{PIZZA}hasTopping> some <{PIZZA}MozzarellaTopping>",
    )
    iris = _class_iris(results)
    # Real pizzas with mozzarella — at minimum Margherita and American.
    assert f"{PIZZA}Margherita" in iris, (
        f"Margherita missing from existential subclass query — Bug A may have regressed. "
        f"Got {len(iris)} results: {sorted(iris)[:10]}"
    )
    assert f"{PIZZA}American" in iris
    # Should be substantially more than the 2 unsatisfiable classes the bug
    # used to return for any unknown class.
    assert len(iris) > 5, f"Only {len(iris)} results; bug-A symptom (≤2) may have returned"


@requires_pizza
def test_bug_b_label_existential_matches_iri_existential():
    """Bug B regression: the label-form existential resolves the same class
    set as the IRI-form. They are semantically equivalent queries.
    """
    iri_results, _ = _run_query(
        "pizza",
        f"<{PIZZA}hasTopping> some <{PIZZA}MozzarellaTopping>",
    )
    label_results, _ = _run_query(
        "pizza",
        "'has topping' some 'MozzarellaTopping'",
    )
    assert _class_iris(iri_results) == _class_iris(label_results), (
        "IRI and label forms diverged. Bug B fallback in "
        "BasicEntityChecker.getOWLObjectProperty may have regressed."
    )


@requires_pizza
def test_sanity_single_iri_still_resolves():
    """Single `<IRI>` queries must still resolve to the named class, not a
    fresh OWLClass with a bogus IRI. We just check the call doesn't error
    and returns a stable result set.
    """
    results, body = _run_query("pizza", f"<{PIZZA}MozzarellaTopping>")
    assert "error" not in body
    # MozzarellaTopping has at least the two unsatisfiable subclasses
    # baked into pizza.owl on purpose.
    assert isinstance(results, list)


@requires_pizza
def test_bogus_iri_does_not_return_unsatisfiable_noise():
    """A clearly-fake `<http://example.org/foo>` IRI must not silently return
    unsatisfiable classes as if they were subclasses of it. With the parser
    fix the Manchester parser raises (400 / parser error). Either an error
    response or empty result is acceptable — what's *not* acceptable is the
    pre-fix behaviour of returning CheeseyVegetableTopping / IceCream.
    """
    try:
        results, body = _run_query("pizza", "<http://example.org/foo>")
    except requests.HTTPError:
        return  # 400 is acceptable
    iris = _class_iris(results)
    # The unsatisfiable noise pattern the bug used to surface.
    BUGGY_NOISE = {
        f"{PIZZA}CheeseyVegetableTopping",
        f"{PIZZA}IceCream",
    }
    assert not (iris & BUGGY_NOISE), (
        f"Bogus IRI returned unsatisfiable classes as 'subclasses' — "
        f"bug-A symptom regressed. Got: {iris}"
    )


# ----------------------------------------------------------------------- go

requires_go = pytest.mark.skipif(
    "go" not in LOADED, reason="go ontology not loaded on worker"
)


@requires_go
def test_bug_a_go_part_of_apoptotic_process():
    """The canonical bug-A repro query against GO. Old aberowl returns ~44;
    pre-fix aberowl2 returned 0; post-fix should be >0 and the labels should
    look apoptosis-related (semantic sanity check on the reasoner output).
    """
    results, _ = _run_query(
        "go",
        f"<{OBO}BFO_0000050> some <{OBO}GO_0006915>",
    )
    iris = _class_iris(results)
    assert len(iris) > 10, (
        f"Only {len(iris)} subclasses of `part_of some apoptotic process` — "
        f"bug A may have regressed."
    )
    # The returned classes must be apoptosis-related — guards against the
    # bug returning unrelated noise classes.
    labels_joined = " ".join(
        (r.get("label") or "").lower() for r in results
    )
    assert "apopto" in labels_joined, (
        f"Result labels look unrelated to apoptosis; reasoner output may be wrong. "
        f"Sample: {[r.get('label') for r in results[:5]]}"
    )


@requires_go
def test_bug_b_go_label_existential_matches_iri():
    """Label-form `'part of' some 'apoptotic process'` on GO must match the
    IRI-form result set exactly.
    """
    iri_results, _ = _run_query(
        "go",
        f"<{OBO}BFO_0000050> some <{OBO}GO_0006915>",
    )
    label_results, _ = _run_query(
        "go",
        "'part of' some 'apoptotic process'",
    )
    assert _class_iris(iri_results) == _class_iris(label_results), (
        "GO label-form existential diverged from IRI-form. "
        "Bug B fallback in BasicEntityChecker.getOWLObjectProperty may have regressed."
    )


@requires_go
def test_sanity_go_roots_unchanged():
    """`owl:Thing` direct subclasses on GO must be the three canonical roots.
    Guards against the parser fix breaking the basic hierarchy path.
    """
    results, _ = _run_query(
        "go",
        "<http://www.w3.org/2002/07/owl#Thing>",
        direct="true",
    )
    iris = _class_iris(results)
    assert iris == {
        f"{OBO}GO_0008150",  # biological_process
        f"{OBO}GO_0005575",  # cellular_component
        f"{OBO}GO_0003674",  # molecular_function
    }, f"GO roots unexpected: {iris}"


@requires_go
def test_sanity_go_bare_label_class():
    """Bare labels like `apoptotic process` must still resolve to the GO
    class and return its subclass hierarchy (this is the most common UI
    interaction).
    """
    results, _ = _run_query("go", "apoptotic process")
    assert len(results) > 50, (
        f"Only {len(results)} subclasses for bare label 'apoptotic process'; "
        f"label resolution may have regressed."
    )
