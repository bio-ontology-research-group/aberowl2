#!/usr/bin/env python3
"""Median response time of a few representative requests, from wherever it runs.

    python3 scripts/probe_latency.py --api https://aber-owl.net --n 15
    python3 scripts/probe_latency.py --api http://127.0.0.1:8000 --n 15   # on the central host

Sequential calls with a short pause; reports median, p95 and minimum in ms.
"""
import argparse, statistics, time, urllib.parse, urllib.request

PROBES = [
    ("resolve 'cell death' in GO", "/api/resolve?" + urllib.parse.urlencode({"query": "cell death", "ontology": "GO"})),
    ("search_all 'apoptosis'", "/api/search_all?query=apoptosis"),
    ("dlquery_all GO:0008219 subeq (97)", "/api/dlquery_all?query=" + urllib.parse.quote("<http://purl.obolibrary.org/obo/GO_0008219>") + "&type=subeq&ontologies=GO&labels=true"),
    ("dlquery_all HP:0000118 subeq (18,690)", "/api/dlquery_all?query=" + urllib.parse.quote("<http://purl.obolibrary.org/obo/HP_0000118>") + "&type=subeq&ontologies=HP&labels=true"),
]


def timed(url):
    start = time.perf_counter()
    urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "aberowl2-probe-latency"}), timeout=180).read()
    return (time.perf_counter() - start) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="https://aber-owl.net")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--pause", type=float, default=0.7)
    a = ap.parse_args()
    for name, path in PROBES:
        xs = sorted(timed(a.api + path) for _ in range(a.n))
        p95 = xs[max(0, int(round(0.95 * len(xs))) - 1)]
        print(f"{name:40s} n={len(xs)} median={statistics.median(xs):7.0f} ms  p95={p95:7.0f} ms  min={xs[0]:6.0f} ms")
        time.sleep(a.pause)


if __name__ == "__main__":
    main()
