#!/usr/bin/env python3
"""Compare the deployed service's answer sets with the released gold sets.

For every row of the gold file, submit its Manchester expression, query type and
ontology to /api/dlquery_all and compare the returned class IRIs with gold_iris,
strictly and with the anchor classes (the queried class or the restriction filler,
which `subeq` returns and the gold sets exclude) set aside. Writes a JSON report.

    python3 check_service_gold.py --gold gold_all.jsonl --out service_gold_agreement.json
"""
import argparse, collections, json, time, urllib.parse, urllib.request


def ask(base, query, qtype, ont, retries=3):
    url = base + "/api/dlquery_all?" + urllib.parse.urlencode(
        {"query": query, "type": qtype, "ontologies": ont, "labels": "true"})
    req = urllib.request.Request(url, headers={"User-Agent": "aberowl2-check-service-gold"})
    for attempt in range(retries):
        try:
            data = json.load(urllib.request.urlopen(req, timeout=180))
            return {(r.get("class") or r.get("owlClass", "")).strip("<>") for r in data.get("result", [])}
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold_all.jsonl")
    ap.add_argument("--api", default="https://aber-owl.net", help="service base URL")
    ap.add_argument("--out", default="service_gold_agreement.json")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between requests")
    a = ap.parse_args()
    gold = [json.loads(l) for l in open(a.gold) if l.strip()]
    counts, rows = collections.Counter(), []
    for g in gold:
        got = ask(a.api, g["manchester"], g["query_type"], g["ontology"])
        anchors, gs = set(g.get("anchor_iris") or []), set(g["gold_iris"])
        exact = got == gs
        normalized = (got - anchors) == (gs - anchors)
        verdict = "exact" if exact else "normalized" if normalized else "mismatch"
        counts[f"{verdict}|{g['task']}"] += 1
        rows.append({"task": g["task"], "ontology": g["ontology"], "term": g["term"],
                     "query_type": g["query_type"], "manchester": g["manchester"],
                     "n_gold": len(gs), "n_service": len(got), "verdict": verdict,
                     "missing": sorted(gs - got), "extra": sorted(got - gs)})
        time.sleep(a.pause)
    report = {"api": a.api, "gold": a.gold, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "n": len(gold), "counts": dict(counts), "rows": rows}
    json.dump(report, open(a.out, "w"), indent=1)
    print(json.dumps({"n": len(gold), "counts": dict(counts)}))


if __name__ == "__main__":
    main()
