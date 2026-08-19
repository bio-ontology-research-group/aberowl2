"""Exact per-model token + cost report from a runs file.

Uses the `prompt_tokens`/`completion_tokens` the harness now logs per run
(× live OpenRouter pricing). Note: runs produced BEFORE usage-logging was added
show 0 tokens — this is for future runs.

    python cost_report.py --runs runs_full.jsonl
"""
import argparse, json, collections
import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.runs) if l.strip()]
    price = {m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
             for m in httpx.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]}
    agg = collections.defaultdict(lambda: [0, 0, 0])   # prompt_tok, completion_tok, runs
    for r in rows:
        v = agg[r["model"]]
        v[0] += r.get("prompt_tokens") or 0
        v[1] += r.get("completion_tokens") or 0
        v[2] += 1
    print(f'{"model":34} {"runs":>5} {"in_tok":>10} {"out_tok":>10} {"$":>8}')
    tot = 0.0
    for m, (pt, ct, n) in sorted(agg.items()):
        pin, pout = price.get(m, (0, 0))
        cost = pt * pin / 1e6 + ct * pout / 1e6
        tot += cost
        print(f'{m:34} {n:>5} {pt:>10} {ct:>10} {cost:>8.3f}')
    print(f'{"TOTAL":34} {"":>5} {"":>10} {"":>10} {tot:>8.3f}')


if __name__ == "__main__":
    main()
