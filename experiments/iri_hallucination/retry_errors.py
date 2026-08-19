"""Re-run only the error records in a runs file and patch them back in place.

Errors are usually transient (a model returns a malformed/empty body instead of
JSON). This re-runs just those (model, condition, regime, item) tuples and
rewrites the file with the good records + the retried ones.

    python retry_errors.py --runs runs_rest.jsonl
"""
import argparse, asyncio, json
import httpx
import config as C
import harness as H


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    a = ap.parse_args()
    if not C.OPENROUTER_API_KEY:
        raise SystemExit("set OPENROUTER_API_KEY")
    rows = [json.loads(l) for l in open(a.runs) if l.strip()]
    ok = [r for r in rows if not r.get("error")]
    bad = [r for r in rows if r.get("error")]
    print(f"{len(bad)} error records to retry (of {len(rows)})")
    if not bad:
        return
    fixed = []
    async with httpx.AsyncClient() as client:
        for r in bad:
            item = {"term": r["term"], "ontology": r.get("ontology"),
                    "gold_iri": r.get("gold_iri"), "difficulty": r.get("difficulty")}
            try:
                res = await H.run_item(client, r["model"], r["condition"], r["regime"], item)
            except Exception as e:
                res = H._result(item, r["model"], r["condition"], r["regime"], "", [],
                                error=f"{type(e).__name__}: {e}")
            fixed.append(res)
            print(f"  {r['model'].split('/')[-1]} {r['regime']}/{r['condition']} "
                  f"{r['term'][:30]!r} -> err={res.get('error')} ans={res['answer'][:40]!r}")
    with open(a.runs, "w") as f:
        for r in ok + fixed:
            f.write(json.dumps(r) + "\n")
    still = sum(1 for r in fixed if r.get("error"))
    print(f"patched {len(fixed)} records; still failing: {still}")


if __name__ == "__main__":
    asyncio.run(main())
