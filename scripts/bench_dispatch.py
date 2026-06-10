#!/usr/bin/env python3
"""Compare central-server dispatch shapes against a multi-ontology worker.

Mode A — "old" (current prod): N parallel HTTP calls, one per ontology,
dispatched via asyncio.gather (mirroring central_server/app/main.py:700).

Mode B — "new" (option B): 1 HTTP call with ontologyIds=a,b,c,...
"""
import argparse, asyncio, json, statistics, sys, time
import aiohttp

BASE = "http://localhost:8081/api"


async def call_single(session, ontology_id, query, qtype, direct, labels):
    params = {
        "ontologyId": ontology_id,
        "query": query,
        "type": qtype,
        "direct": direct,
        "labels": labels,
    }
    async with session.get(f"{BASE}/runQuery.groovy", params=params, timeout=30) as r:
        body = await r.json()
        return body.get("result", [])


async def mode_old(session, onts, query, qtype, direct, labels):
    """Fan-out N HTTP calls, gather all."""
    tasks = [
        call_single(session, o, query, qtype, direct, labels) for o in onts
    ]
    chunks = await asyncio.gather(*tasks)
    out = []
    for c, ont in zip(chunks, onts):
        for entry in c:
            entry["ontology"] = ont
        out.extend(c)
    return out


async def mode_new(session, onts, query, qtype, direct, labels):
    """One HTTP call with ontologyIds=a,b,c."""
    params = {
        "ontologyIds": ",".join(onts),
        "query": query,
        "type": qtype,
        "direct": direct,
        "labels": labels,
    }
    async with session.get(f"{BASE}/runQuery.groovy", params=params, timeout=30) as r:
        body = await r.json()
        return body.get("result", [])


async def time_run(coro_factory, n, warmup):
    times = []
    last_count = 0
    async with aiohttp.ClientSession() as session:
        for i in range(warmup + n):
            t0 = time.perf_counter()
            r = await coro_factory(session)
            ms = (time.perf_counter() - t0) * 1000
            last_count = len(r)
            if i >= warmup:
                times.append(ms)
    times.sort()
    return {
        "n_results": last_count,
        "mean": round(statistics.mean(times), 1),
        "median": round(statistics.median(times), 1),
        "p95": round(times[int(len(times)*0.95) if len(times) > 1 else 0], 1),
        "min": round(times[0], 1),
        "max": round(times[-1], 1),
    }


CASES = [
    # (label, query, type, direct, labels)
    ("owl:Thing roots (direct=true)",
     "<http://www.w3.org/2002/07/owl#Thing>", "subclass", "true", "true"),
    ("GO existential (only go has axioms)",
     "<http://purl.obolibrary.org/obo/BFO_0000050> some <http://purl.obolibrary.org/obo/GO_0006915>",
     "subclass", "false", "true"),
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument(
        "--ontologies", default="pizza,go,bfo,ro,iao,so",
        help="comma-separated ontology ids loaded on local worker"
    )
    args = ap.parse_args()
    onts = [o.strip() for o in args.ontologies.split(",") if o.strip()]
    print(f"Worker: {BASE}")
    print(f"Ontologies: {onts}")
    print(f"Runs: {args.n} (after {args.warmup} warmup)\n")

    for label, query, qtype, direct, labels in CASES:
        print(f"=== {label} ===")
        old = await time_run(
            lambda s, q=query, t=qtype, d=direct, l=labels:
                mode_old(s, onts, q, t, d, l),
            args.n, args.warmup,
        )
        new = await time_run(
            lambda s, q=query, t=qtype, d=direct, l=labels:
                mode_new(s, onts, q, t, d, l),
            args.n, args.warmup,
        )
        print(f"  {'mode':<8} {'n':>4} {'mean':>8} {'med':>8} {'p95':>8} {'min':>8} {'max':>8}")
        print(f"  {'old (N calls)':<8} {old['n_results']:>4} {old['mean']:>8.1f} {old['median']:>8.1f} {old['p95']:>8.1f} {old['min']:>8.1f} {old['max']:>8.1f}")
        print(f"  {'new (1 call)':<8} {new['n_results']:>4} {new['mean']:>8.1f} {new['median']:>8.1f} {new['p95']:>8.1f} {new['min']:>8.1f} {new['max']:>8.1f}")
        ratio = old['mean'] / new['mean'] if new['mean'] > 0 else float('inf')
        print(f"  speedup: {ratio:.2f}x")
        if old['n_results'] != new['n_results']:
            print(f"  WARNING: result counts differ — {old['n_results']} vs {new['n_results']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
