#!/usr/bin/env python3
"""Quick latency comparison: new beta (aberowl2) vs old aberowl, same DL queries."""
import argparse, statistics, time, sys
import requests
from urllib.parse import quote

NEW = "https://beta.aber-owl.net"
OLD = "http://aberowl.aber-owl.net"

OWL_THING = "<http://www.w3.org/2002/07/owl#Thing>"

# Endpoint contracts differ:
#   new: /api/dlquery_all?ontologies=<lower>&...
#   old: /api/dlquery?ontology=<UPPER>&...
def url_new(ont_id, query, qtype="subclass", direct="false"):
    return f"{NEW}/api/dlquery_all?ontologies={ont_id}&type={qtype}&direct={direct}&labels=true&query={quote(query)}"

def url_old(ont_id, query, qtype="subclass", direct="false"):
    return f"{OLD}/api/dlquery?ontology={ont_id}&type={qtype}&direct={direct}&query={quote(query)}"

CASES = [
    # (label, new-ont-id, old-ont-id, query, qtype, direct)
    ("GO roots",                       "go",    "GO",    OWL_THING, "subclass", "true"),
    ("GO 'part of' apoptotic process", "go",    "GO",    "<http://purl.obolibrary.org/obo/BFO_0000050> some <http://purl.obolibrary.org/obo/GO_0006915>", "subclass", "false"),
    ("MONDO roots",                    "mondo", "MONDO", OWL_THING, "subclass", "true"),
    ("CHEBI roots",                    "chebi", "CHEBI", OWL_THING, "subclass", "true"),
    ("NCIT roots",                     "ncit",  "NCIT",  OWL_THING, "subclass", "true"),
    ("FMA roots",                      "fma",   "FMA",   OWL_THING, "subclass", "true"),
]

def run(session, url, n, warmup, timeout):
    times, count = [], None
    for i in range(warmup + n):
        try:
            t0 = time.perf_counter()
            r = session.get(url, timeout=timeout)
            ms = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
            body = r.json()
            count = len(body.get("result") or [])
            if i >= warmup:
                times.append(ms)
        except Exception as e:
            return {"error": str(e)}
    times.sort()
    return {
        "n_results": count,
        "mean":   round(statistics.mean(times), 1),
        "median": round(statistics.median(times), 1),
        "p95":    round(times[int(len(times)*0.95) if len(times) > 1 else 0], 1),
        "min":    round(times[0], 1),
        "max":    round(times[-1], 1),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    s = requests.Session()
    print(f"{'case':<38} {'side':<5} {'n':>4} {'mean':>8} {'med':>8} {'p95':>8} {'min':>8} {'max':>8}")
    print("-" * 92)
    for label, new_id, old_id, query, qtype, direct in CASES:
        new = run(s, url_new(new_id, query, qtype, direct), args.n, args.warmup, args.timeout)
        old = run(s, url_old(old_id, query, qtype, direct), args.n, args.warmup, args.timeout)
        for side, r in (("NEW", new), ("OLD", old)):
            if "error" in r:
                print(f"{label:<38} {side:<5} ERROR: {r['error']}")
            else:
                print(f"{label:<38} {side:<5} {r['n_results']:>4} {r['mean']:>8.1f} {r['median']:>8.1f} {r['p95']:>8.1f} {r['min']:>8.1f} {r['max']:>8.1f}")
        if "error" not in new and "error" not in old:
            ratio = new['mean'] / old['mean'] if old['mean'] > 0 else float('inf')
            print(f"{'':<38} {'':<5} ratio NEW/OLD = {ratio:.1f}x")
        print()

if __name__ == "__main__":
    sys.exit(main())
