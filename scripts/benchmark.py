#!/usr/bin/env python3
"""
Benchmark key AberOWL API endpoints before/after performance fixes.

Measures wall-clock latency (ms) for the endpoints most affected by the
O(n) Redis scan and worker-side toInfo() issues:

  1. getOntology         — O(n) Redis hvals scan, find one entry
  2. dlquery_all roots   — O(n) scan + worker: direct subclasses of owl:Thing
  3. dlquery_all class   — O(n) scan + worker: subclasses of a named class
  4. getClass            — ES lookup (should be fast regardless)

Usage:
    python scripts/benchmark.py                          # defaults
    python scripts/benchmark.py --ontology go --n 30
    python scripts/benchmark.py --url http://localhost:8000 --ontology pizza --n 20
    python scripts/benchmark.py --json results.json      # save raw timings
"""

import argparse
import json
import statistics
import sys
import time
from typing import Callable

import requests

OWL_THING = "http://www.w3.org/2002/07/owl#Thing"

# A class IRI that exists in GO; override with --class-iri for other ontologies.
DEFAULT_CLASS_IRI = "http://purl.obolibrary.org/obo/GO_0008150"  # biological_process


def timed_get(session: requests.Session, url: str, params: dict) -> float:
    """Return elapsed time in ms for a successful GET, or raise on error."""
    t0 = time.perf_counter()
    r = session.get(url, params=params, timeout=60)
    elapsed = (time.perf_counter() - t0) * 1000
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {url} params={params}")
    return elapsed


def run_scenario(
    label: str,
    fn: Callable[[], float],
    n: int,
    warmup: int = 2,
) -> dict:
    """Run fn() n+warmup times; discard warmup, return stats dict."""
    print(f"  {label} ... ", end="", flush=True)
    times = []
    for i in range(warmup + n):
        try:
            ms = fn()
            if i >= warmup:
                times.append(ms)
        except Exception as e:
            print(f"\n    SKIP (error: {e})")
            return {"label": label, "error": str(e)}

    times.sort()
    result = {
        "label": label,
        "n": n,
        "mean_ms": round(statistics.mean(times), 1),
        "median_ms": round(statistics.median(times), 1),
        "p95_ms": round(times[int(len(times) * 0.95)], 1),
        "p99_ms": round(times[min(int(len(times) * 0.99), len(times) - 1)], 1),
        "min_ms": round(times[0], 1),
        "max_ms": round(times[-1], 1),
    }
    print(f"mean={result['mean_ms']}ms  median={result['median_ms']}ms  "
          f"p95={result['p95_ms']}ms  p99={result['p99_ms']}ms")
    return result


def count_registry_entries(base_url: str, session: requests.Session) -> int:
    try:
        r = session.get(f"{base_url}/api/servers", timeout=10)
        return len(r.json())
    except Exception:
        return -1


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark AberOWL API endpoints.")
    parser.add_argument("--url", default="http://localhost:8000", help="Central server base URL")
    parser.add_argument("--ontology", default="go", help="Ontology ID to query against")
    parser.add_argument("--class-iri", default=DEFAULT_CLASS_IRI,
                        help="Class IRI for the named-class DL query scenario")
    parser.add_argument("--n", type=int, default=20, help="Iterations per scenario (excl. warmup)")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup iterations to discard")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="Save raw results to this JSON file")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    ont = args.ontology
    class_iri = args.class_iri
    class_q = f"<{class_iri}>"

    session = requests.Session()

    # Check server is up
    try:
        session.get(f"{base}/api/servers", timeout=5).raise_for_status()
    except Exception as e:
        print(f"Cannot reach central server at {base}: {e}", file=sys.stderr)
        sys.exit(1)

    n_entries = count_registry_entries(base, session)
    print(f"\nBenchmarking {base}  ontology={ont}  registry_size={n_entries}  n={args.n}\n")

    scenarios = [
        (
            "getOntology (O(n) Redis scan)",
            lambda: timed_get(session, f"{base}/api/getOntology", {"ontology": ont}),
        ),
        (
            "dlquery_all owl:Thing direct subclasses (root hierarchy load)",
            lambda: timed_get(session, f"{base}/api/dlquery_all", {
                "query": f"<{OWL_THING}>",
                "type": "subclass",
                "ontologies": ont,
                "direct": "true",
                "labels": "true",
            }),
        ),
        (
            f"dlquery_all named class subclasses ({class_iri.rsplit('/', 1)[-1]})",
            lambda: timed_get(session, f"{base}/api/dlquery_all", {
                "query": class_q,
                "type": "subclass",
                "ontologies": ont,
                "direct": "true",
                "labels": "true",
            }),
        ),
        (
            "getClass ES lookup",
            lambda: timed_get(session, f"{base}/api/getClass", {
                "query": class_iri,
                "ontology": ont,
            }),
        ),
        (
            "listOntologies (O(n) Redis scan)",
            lambda: timed_get(session, f"{base}/api/listOntologies", {}),
        ),
    ]

    results = []
    print("Scenarios:")
    for label, fn in scenarios:
        r = run_scenario(label, fn, n=args.n, warmup=args.warmup)
        results.append(r)

    summary = {
        "base_url": base,
        "ontology": ont,
        "registry_size": n_entries,
        "n_iterations": args.n,
        "results": results,
    }

    print(f"\nRegistry entries at time of run: {n_entries}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Raw results saved to {args.json_out}")


if __name__ == "__main__":
    main()
