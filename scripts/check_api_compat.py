#!/usr/bin/env python3
"""
check_api_compat.py — measure how much of the AberOWL 1 REST API a given
AberOWL deployment still serves.

Why this exists
---------------
AberOWL 2 changed the API paths, so downstream consumers (Bioregistry, BARTOC,
tetherless-world/voo, and several of our own services) silently broke. The plan
in `API_V1_COMPAT_PLAN.md` restores the v1 surface. This script is the metric
for that work: it replays the twelve operations AberOWL 1 declared in its own
OpenAPI spec and reports, per operation, whether the response still matches the
v1 contract.

The operation list and the expected response shapes are NOT reconstructed from
memory. They come from two artifacts:

  * `~/Git/aberowlweb/aberowlweb/static/openapi/schema.yml` — the OpenAPI 3.0
    spec the old Django app served at its own /docs. Twelve operations.
  * a real archived response, `tests/fixtures/aberowl_v1_ontology_list.json`
    (web.archive.org snapshot 20221120122151 of
    http://aber-owl.net/api/ontology/?drf_fromat=json&format=json).

Usage
-----
    # against a running deployment
    python3 scripts/check_api_compat.py http://aber-owl.net
    python3 scripts/check_api_compat.py http://localhost:8000

    # against the FastAPI app in this repo, no server needed (used by CI)
    python3 scripts/check_api_compat.py --in-process

    # machine-readable, for the committed before/after report pair
    python3 scripts/check_api_compat.py http://aber-owl.net \
        --json --out results/api_compat/prod-baseline-2026-08-27.json

Exit status is 0 when every operation passes, 1 otherwise, so it can gate a
deploy step.

Standard library only, so it runs anywhere without an environment.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_TIMEOUT = 30

# Ontologies used as probes. GO is present in every AberOWL deployment we care
# about; pizza is what the local test worker loads. The checker falls back from
# one to the next so the same invocation works against prod and a laptop.
PROBE_ONTOLOGIES = ("GO", "PIZZA")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


class Check:
    """One v1 operation and the verdict on it."""

    def __init__(self, operation: str, path: str):
        self.operation = operation
        self.path = path
        self.status = FAIL
        self.detail = "not run"
        self.http_status: int | None = None

    def ok(self, detail: str) -> "Check":
        self.status, self.detail = PASS, detail
        return self

    def fail(self, detail: str) -> "Check":
        self.status, self.detail = FAIL, detail
        return self

    def skip(self, detail: str) -> "Check":
        self.status, self.detail = SKIP, detail
        return self

    def as_dict(self) -> dict:
        return {
            "operation": self.operation,
            "path": self.path,
            "status": self.status,
            "http_status": self.http_status,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Transports: a live URL, or the FastAPI app in-process
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect instead of following it.

    Used only for the /api/sparql check, whose whole point is to observe a 302.
    urllib follows redirects by default, so without this the checker would report
    whatever the external endpoint returned.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    """GET/POST against a running deployment."""

    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener()
        self._no_redirect_opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, path: str, params=None, body=None,
                follow_redirects: bool = True):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            opener = self._opener if follow_redirects else self._no_redirect_opener
            with opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, _decode(raw)
        except urllib.error.HTTPError as e:
            return e.code, _decode(e.read())
        except Exception as e:  # DNS failure, timeout, refused connection
            return None, {"__transport_error__": str(e)}


class InProcessTransport:
    """Drive `central_server/app/main.py` directly, no server and no Docker.

    Mirrors the fixture in tests/test_central_api.py: swap in a minimal async
    Redis stand-in and a mocked ES manager, then talk to the ASGI app over
    httpx's ASGITransport.
    """

    def __init__(self):
        import logging
        import os
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo / "central_server"))
        os.environ.setdefault("ENABLE_MCP", "false")
        logging.disable(logging.INFO)

        import app.main as main_module  # noqa: E402

        # Seed one hosted ontology so registry-backed operations have something
        # to return; an empty registry makes the run report "empty list" for
        # reasons that have nothing to do with the contract.
        redis = _FakeRedis()
        redis._data[main_module.REGISTRY_KEY] = {
            "go": json.dumps({
                "ontology": "go", "ontology_id": "go", "title": "Gene Ontology",
                "description": "The Gene Ontology.",
                "home_page": "http://geneontology.org",
                "version_info": "in-process", "url": "http://go-worker:80",
                "status": "online", "class_count": 47000,
            }),
        }
        main_module.redis_client = redis
        main_module.es_mgr = _StubES()
        self._app = main_module.app

    def request(self, method: str, path: str, params=None, body=None,
                follow_redirects: bool = True):
        import asyncio

        from httpx import ASGITransport, AsyncClient

        async def go():
            transport = ASGITransport(app=self._app)
            async with AsyncClient(transport=transport, base_url="http://test",
                                   follow_redirects=follow_redirects) as c:
                r = await c.request(method, path, params=params, json=body)
                return r.status_code, _decode(r.content)

        return asyncio.run(go())


class _StubES:
    """Async no-op Elasticsearch stand-in.

    In-process mode has no Elasticsearch, so search returns nothing. That is
    enough to exercise the response *shape*, which is what this checker asserts;
    a MagicMock cannot be awaited and would crash the handler instead.
    """

    async def search_classes(self, term, ontology=None, prefix=False, size=100):
        return []

    async def search_ontologies(self, term, size=50):
        return []

    def _alias_name(self, ontology_id):
        return f"aberowl_{ontology_id}_classes"


class _FakeRedis:
    """Just enough async Redis for the registry reads the endpoints perform."""

    def __init__(self):
        self._data: dict[str, dict[str, str]] = {}

    async def ping(self):
        return True

    async def hset(self, name, key, value):
        self._data.setdefault(name, {})[key] = value

    async def hget(self, name, key):
        return self._data.get(name, {}).get(key)

    async def hvals(self, name):
        return list(self._data.get(name, {}).values())

    async def hkeys(self, name):
        return list(self._data.get(name, {}).keys())

    async def close(self):
        pass


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"__non_json__": raw[:200].decode("utf-8", "replace")}


# ---------------------------------------------------------------------------
# Shape assertions, derived from the archived v1 response and schema.yml
# ---------------------------------------------------------------------------

# Keys Bioregistry's getter reads (bioregistry/external/aberowl/__init__.py).
BIOREGISTRY_TOP_KEYS = ("acronym", "name")
BIOREGISTRY_SUBMISSION_KEYS = ("home_page", "description", "version", "download_url")

# v1's success envelope for the query endpoints.
V1_ENVELOPE_KEYS = ("status", "result")


def _spa_shell(body: Any) -> str | None:
    """Describe the "200 but it's the web page" failure, or None.

    The central server's catch-all `/{path}` route serves the SPA's index.html,
    so every unimplemented /api/ path answers 200 with HTML rather than 404.
    Consumers see a successful request and parse garbage — which is how the
    AberOWL 1 API came to look "removed" from outside. Name it explicitly;
    it is the headline finding of the baseline.
    """
    if isinstance(body, dict) and "__non_json__" in body:
        snippet = str(body["__non_json__"]).lstrip()[:40].replace("\n", " ")
        if snippet.lower().startswith("<!doctype") or snippet.lower().startswith("<html"):
            return "HTTP 200 serving the SPA HTML shell, not JSON"
        return f"HTTP 200 but the body is not JSON: {snippet!r}"
    return None


def _v1_envelope(body: Any) -> str | None:
    """Return an error description, or None when the body matches v1's envelope."""
    shell = _spa_shell(body)
    if shell:
        return shell
    if not isinstance(body, dict):
        return f"expected a JSON object, got {type(body).__name__}"
    missing = [k for k in V1_ENVELOPE_KEYS if k not in body]
    if missing:
        return f"missing v1 envelope keys {missing}; got {sorted(body)[:8]}"
    if not isinstance(body["result"], list):
        return "'result' is not a list"
    return None


# ---------------------------------------------------------------------------
# The twelve operations
# ---------------------------------------------------------------------------

def check_list_ontologies(t) -> Check:
    """GET /api/ontology — the endpoint Bioregistry harvests."""
    c = Check("GET /api/ontology/", "/api/ontology/")
    status, body = t.request("GET", "/api/ontology/", params={"format": "json"})
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    shell = _spa_shell(body)
    if shell:
        return c.fail(shell)
    if not isinstance(body, list):
        return c.fail(f"expected a JSON list, got {type(body).__name__}")
    if not body:
        return c.fail("empty list")
    entry = body[0]
    missing = [k for k in BIOREGISTRY_TOP_KEYS if k not in entry]
    if missing:
        return c.fail(f"entry missing {missing}; got {sorted(entry)[:8]}")
    sub = entry.get("submission")
    if not isinstance(sub, dict):
        return c.fail("entry has no 'submission' object (Bioregistry reads it)")
    missing_sub = [k for k in BIOREGISTRY_SUBMISSION_KEYS if k not in sub]
    if missing_sub:
        return c.fail(f"submission missing {missing_sub}")
    return c.ok(f"{len(body)} entries, Bioregistry keys present")


def check_find_ontology(t) -> Check:
    """GET /api/ontology/_find — v1 returned a bare list, not an envelope."""
    c = Check("GET /api/ontology/_find", "/api/ontology/_find")
    status, body = t.request("GET", "/api/ontology/_find", params={"query": "gene"})
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    shell = _spa_shell(body)
    if shell:
        return c.fail(shell)
    if not isinstance(body, list):
        return c.fail(f"expected a bare JSON list, got {type(body).__name__}")
    return c.ok(f"{len(body)} hits")


def check_class_find(t) -> Check:
    c = Check("GET /api/class/_find", "/api/class/_find")
    status, body = t.request("GET", "/api/class/_find", params={"query": "apoptosis"})
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    err = _v1_envelope(body)
    if err:
        return c.fail(err)
    return c.ok(f"{len(body['result'])} hits")


def check_class_startwith(t, ontology: str) -> Check:
    c = Check("GET /api/class/_startwith", "/api/class/_startwith")
    status, body = t.request(
        "GET", "/api/class/_startwith", params={"query": "cell", "ontology": ontology}
    )
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    err = _v1_envelope(body)
    if err:
        return c.fail(err)
    return c.ok(f"{len(body['result'])} hits for ontology={ontology}")


def check_class_similar(t, ontology: str) -> Check:
    """Planned for removal — a 410 with v1's error envelope is the pass."""
    c = Check("GET /api/class/_similar", "/api/class/_similar")
    status, body = t.request(
        "GET",
        "/api/class/_similar",
        params={"ontology": ontology, "class": "cell", "size": 5},
    )
    c.http_status = status
    if status == 410 and isinstance(body, dict) and "message" in body:
        return c.ok("410 with an explanatory message (retired, as planned)")
    return c.fail(_spa_shell(body) or f"HTTP {status}; expected 410 + v1 error envelope")


def check_dlquery(t, ontology: str) -> Check:
    c = Check("GET /api/dlquery", "/api/dlquery")
    status, body = t.request(
        "GET",
        "/api/dlquery",
        params={"query": "<http://www.w3.org/2002/07/owl#Thing>",
                "type": "subclass", "ontology": ontology, "direct": "true"},
    )
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    err = _v1_envelope(body)
    if err:
        return c.fail(err)
    return c.ok(f"{len(body['result'])} results for ontology={ontology}")


def check_dlquery_logs(t) -> Check:
    """Planned for removal — a 410 with v1's error envelope is the pass."""
    c = Check("GET /api/dlquery/logs", "/api/dlquery/logs")
    status, body = t.request("GET", "/api/dlquery/logs")
    c.http_status = status
    if status == 410 and isinstance(body, dict) and "message" in body:
        return c.ok("410 with an explanatory message (retired, as planned)")
    return c.fail(_spa_shell(body) or f"HTTP {status}; expected 410 + v1 error envelope")


def check_root(t, ontology: str) -> Check:
    c = Check("GET /api/ontology/{acronym}/root/{class_iri}",
              f"/api/ontology/{ontology}/root/…")
    # %23, not a bare '#': that character starts the URL fragment, so a literal
    # one means the client sends ".../root/http://www.w3.org/2002/07/owl" and
    # drops "Thing" entirely. The worker then does not recognise the IRI and the
    # check fails against a perfectly healthy deployment.
    iri = "http://purl.obolibrary.org/obo/GO_0008150"
    status, body = t.request("GET", f"/api/ontology/{ontology}/root/{iri}")
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    err = _v1_envelope(body)
    if err:
        return c.fail(err)
    return c.ok(f"{len(body['result'])} roots")


def check_object_properties(t, ontology: str) -> Check:
    c = Check("GET /api/ontology/{acronym}/objectproperty",
              f"/api/ontology/{ontology}/objectproperty")
    status, body = t.request("GET", f"/api/ontology/{ontology}/objectproperty")
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    err = _v1_envelope(body)
    if err:
        return c.fail(err)
    return c.ok(f"{len(body['result'])} object properties")


def check_object_property_detail(t, ontology: str) -> Check:
    c = Check("GET /api/ontology/{acronym}/objectproperty/{property_iri}",
              f"/api/ontology/{ontology}/objectproperty/…")
    iri = "http://purl.obolibrary.org/obo/BFO_0000050"
    status, body = t.request("GET", f"/api/ontology/{ontology}/objectproperty/{iri}")
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    shell = _spa_shell(body)
    if shell:
        return c.fail(shell)
    if not isinstance(body, dict) or "status" not in body:
        return c.fail("expected a v1 envelope object")
    return c.ok("returns a v1 envelope")


def check_match_superclasses(t, ontology: str) -> Check:
    """Used in production by phenotype-reactor."""
    c = Check("POST /api/ontology/{acronym}/class/_matchsuperclasses",
              f"/api/ontology/{ontology}/class/_matchsuperclasses")
    payload = {
        "source_classes": ["http://purl.obolibrary.org/obo/GO_0006915"],
        "target_classes": ["http://purl.obolibrary.org/obo/GO_0008219"],
    }
    status, body = t.request(
        "POST", f"/api/ontology/{ontology}/class/_matchsuperclasses", body=payload
    )
    c.http_status = status
    if status != 200:
        return c.fail(f"HTTP {status}")
    shell = _spa_shell(body)
    if shell:
        return c.fail(shell)
    if not isinstance(body, dict) or "result" not in body:
        return c.fail(f"expected {{'result': …}}; got {sorted(body)[:8] if isinstance(body, dict) else type(body).__name__}")
    return c.ok("returns {'result': …}")


def check_sparql(t) -> Check:
    """AberOWL 1 rewrote the OWL frame and redirected to the endpoint named in
    the query; it never executed SPARQL itself. A v1 query therefore has to come
    back as a 302 to that endpoint, carrying the rewritten query.

    The failure this catches is the silent one: HTTP 200 with the caller's own
    query echoed back, which is what a v1 caller receives when the compatibility
    layer is absent.
    """
    c = Check("GET /api/sparql", "/api/sparql")
    # A v1 frame carries the target endpoint between the query type and the
    # ontology, which is what makes the redirect possible.
    query = (
        "SELECT ?x WHERE { VALUES ?x { OWL subeq "
        "<https://sparql.uniprot.org/sparql> <GO> { 'cell death' } } }"
    )
    # The 302 is the thing being checked, so this one call must not follow it.
    # Everything else follows redirects: a deployment may sit behind an edge that
    # issues its own (http->https, trailing slash), and refusing those globally
    # makes every check fail for reasons unrelated to the contract.
    status, body = t.request("GET", "/api/sparql",
                             params={"query": query, "format": "json"},
                             follow_redirects=False)
    c.http_status = status
    if status in (301, 302, 303, 307, 308):
        return c.ok("redirects to the endpoint named in the query, as v1 did")
    if status == 200 and isinstance(body, dict) and "rewritten_query" in body:
        return c.fail("HTTP 200 echoing the query back — silently does nothing")
    if status == 400 and isinstance(body, dict) and "message" in body:
        # No worker for the frame's ontology: the layer is present and answering,
        # but this deployment cannot resolve it.
        return c.fail(f"HTTP 400: {body['message'][:70]}")
    return c.fail(_spa_shell(body) or f"HTTP {status}; expected a redirect")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def pick_ontology(t) -> str | None:
    """First probe ontology this deployment actually serves."""
    for oid in PROBE_ONTOLOGIES:
        status, body = t.request("GET", "/api/getOntology", params={"ontology": oid})
        if status == 200 and isinstance(body, dict) and body:
            return oid
    return None


def run_all(t) -> list[Check]:
    ontology = pick_ontology(t)
    checks: list[Check] = [
        check_list_ontologies(t),
        check_find_ontology(t),
        check_class_find(t),
        check_dlquery_logs(t),
        check_sparql(t),
    ]

    # (operation label, check) — the label is used when the check has to be
    # skipped, so a SKIP row still names the v1 operation it stands for.
    needs_ontology: list[tuple[str, Callable[[Any, str], Check]]] = [
        ("GET /api/class/_startwith", check_class_startwith),
        ("GET /api/class/_similar", check_class_similar),
        ("GET /api/dlquery", check_dlquery),
        ("GET /api/ontology/{acronym}/root/{class_iri}", check_root),
        ("GET /api/ontology/{acronym}/objectproperty", check_object_properties),
        ("GET /api/ontology/{acronym}/objectproperty/{property_iri}",
         check_object_property_detail),
        ("POST /api/ontology/{acronym}/class/_matchsuperclasses",
         check_match_superclasses),
    ]
    if ontology is None:
        reason = (f"no probe ontology available "
                  f"(tried {', '.join(PROBE_ONTOLOGIES)})")
        checks += [Check(label, "-").skip(reason) for label, _ in needs_ontology]
    else:
        checks += [fn(t, ontology) for _, fn in needs_ontology]
    return checks


def render(checks: list[Check], target: str) -> str:
    width = max(len(c.operation) for c in checks) + 2
    lines = [f"AberOWL 1 API compatibility — target: {target}", ""]
    for c in checks:
        http = f"[{c.http_status}]" if c.http_status is not None else "[---]"
        lines.append(f"  {c.status:<4} {http:<6} {c.operation:<{width}} {c.detail}")
    passed = sum(1 for c in checks if c.status == PASS)
    lines += ["", f"  {passed}/{len(checks)} operations match the AberOWL 1 contract"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("base_url", nargs="?",
                    help="deployment to check, e.g. http://aber-owl.net")
    ap.add_argument("--in-process", action="store_true",
                    help="drive central_server/app/main.py directly; no server needed")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--out", help="also write the JSON report to this path")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()

    if args.in_process:
        transport, target = InProcessTransport(), "in-process (app.main)"
    elif args.base_url:
        transport, target = HttpTransport(args.base_url, args.timeout), args.base_url
    else:
        ap.error("give a base URL or --in-process")

    checks = run_all(transport)
    passed = sum(1 for c in checks if c.status == PASS)

    report = {
        "target": target,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "total": len(checks),
        "checks": [c.as_dict() for c in checks],
    }

    if args.out:
        from pathlib import Path

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")

    print(json.dumps(report, indent=2) if args.json else render(checks, target))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
