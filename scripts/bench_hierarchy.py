#!/usr/bin/env python3
"""Focused hierarchy-load benchmark.

Times the exact endpoint the UI hits when a user clicks an ontology:
GET /api/dlquery_all?query=<owl:Thing>&type=subclass&direct=true&labels=true&ontologies=<id>

Also times one named-class expansion (clicking a sub-node).

Usage:
    python scripts/bench_hierarchy.py --url https://beta.aber-owl.net \
        --ontologies go,hp,mondo --n 10 \
        --json results/deploy_YYYYMMDD/before.json
"""
import argparse, json, statistics, time, sys
import requests

OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
# Per-ontology probe class for "click a child" scenario.
NAMED_CLASS = {
    "go":    "http://purl.obolibrary.org/obo/GO_0008150",   # biological_process
    "hp":    "http://purl.obolibrary.org/obo/HP_0000118",   # phenotypic abnormality
    "mondo": "http://purl.obolibrary.org/obo/MONDO_0000001",
    "mp":    "http://purl.obolibrary.org/obo/MP_0000001",
    "uberon":"http://purl.obolibrary.org/obo/UBERON_0001062",
    "ncit":  "http://purl.obolibrary.org/obo/NCIT_C7057",
}

def timed(session, url, params, timeout):
    t0 = time.perf_counter()
    r = session.get(url, params=params, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    n_results = 0
    try:
        body = r.json()
        n_results = len(body.get("result") or body.get("results") or [])
    except Exception:
        pass
    return ms, n_results

def run(label, fn, n, warmup):
    print(f"  {label:55s} ", end="", flush=True)
    times, last_n = [], None
    for i in range(warmup + n):
        try:
            ms, last_n = fn()
            if i >= warmup:
                times.append(ms)
        except Exception as e:
            print(f"\n    SKIP: {e}")
            return {"label": label, "error": str(e)}
    times.sort()
    out = {
        "label": label, "n": n, "n_results": last_n,
        "mean_ms":   round(statistics.mean(times), 1),
        "median_ms": round(statistics.median(times), 1),
        "p95_ms":    round(times[int(len(times)*0.95) if len(times) > 1 else 0], 1),
        "min_ms":    round(times[0], 1),
        "max_ms":    round(times[-1], 1),
    }
    print(f"mean={out['mean_ms']:>7.1f}  med={out['median_ms']:>7.1f}  "
          f"p95={out['p95_ms']:>7.1f}  min={out['min_ms']:>7.1f}  n_results={last_n}")
    return out

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://beta.aber-owl.net")
    p.add_argument("--ontologies", default="go,hp,mondo")
    p.add_argument("--n", type=int, default=10)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--timeout", type=int, default=90)
    p.add_argument("--json", dest="json_out", default=None)
    args = p.parse_args()

    base = args.url.rstrip("/")
    session = requests.Session()
    out = {"base_url": base, "n": args.n, "warmup": args.warmup, "scenarios": []}
    ontologies = [o.strip() for o in args.ontologies.split(",") if o.strip()]

    for ont in ontologies:
        print(f"\n== ontology={ont} ==")
        # Root hierarchy (owl:Thing direct subclasses)
        r1 = run(
            f"[{ont}] roots (owl:Thing direct subclasses)",
            lambda o=ont: timed(session, f"{base}/api/dlquery_all", {
                "query": f"<{OWL_THING}>", "type": "subclass",
                "ontologies": o, "direct": "true", "labels": "true",
            }, args.timeout),
            args.n, args.warmup)
        r1["ontology"] = ont
        r1["scenario"] = "roots"
        out["scenarios"].append(r1)

        # Named-class expansion (user clicks a tree node)
        iri = NAMED_CLASS.get(ont)
        if iri:
            r2 = run(
                f"[{ont}] expand {iri.rsplit('/',1)[-1]} direct subclasses",
                lambda o=ont, q=iri: timed(session, f"{base}/api/dlquery_all", {
                    "query": f"<{q}>", "type": "subclass",
                    "ontologies": o, "direct": "true", "labels": "true",
                }, args.timeout),
                args.n, args.warmup)
            r2["ontology"] = ont
            r2["scenario"] = "expand_named"
            r2["named_iri"] = iri
            out["scenarios"].append(r2)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nSaved {args.json_out}")

if __name__ == "__main__":
    sys.exit(main())
