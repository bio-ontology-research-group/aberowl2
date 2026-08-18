"""Run the DL-reasoning experiment for EXACTLY ONE model, and record the credit delta.

Deliberately refuses to run more than one model per invocation. The IRI experiment
was budgeted from a projection and gemini-3.5-flash then consumed ~$24 of a ~$26.4
run, 4x the estimate, because its extended thinking is on by default. One model per
invocation, with credits recorded and a human check between models, makes that
failure mode impossible to repeat silently.

Recommended order (ascending cost) is config.MODELS.

Usage:
    OPENROUTER_API_KEY=... python run_model.py --model openai/gpt-oss-20b \
        --gold gold_all.jsonl --out runs_gpt-oss-20b.jsonl
"""
import argparse
import asyncio
import importlib.util
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
# Local config/prompts must win over the ones next to the shared harness.
sys.path.insert(0, HERE)
import config as C  # noqa: E402

CREDITS_LOG = os.path.join(HERE, "credits_log.jsonl")


def _load_shared_harness():
    """Load ../iri_hallucination/harness.py without duplicating it.

    It does `import config as C` / `import prompts as P` at module scope, and HERE
    is first on sys.path, so it binds THIS experiment's config and prompts.
    """
    src = os.path.join(HERE, "..", "iri_hallucination", "harness.py")
    if not os.path.exists(src):
        sys.exit(f"shared harness not found at {src}")
    spec = importlib.util.spec_from_file_location("_shared_harness", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def credits() -> dict:
    """Credit state for THIS KEY. Returns {} if unavailable (never fatal).

    Must use /auth/key, not /credits. The key carries its own spending cap, and
    /credits reports the ACCOUNT total, which can be two orders of magnitude larger:
    observed 2026-08-18 was $819.70 account-wide against $5.57 left on the key. The
    key limit is what actually stops a run, so that is what gets reported.
    """
    try:
        r = httpx.get(C.OPENROUTER_KEY_URL,
                      headers={"Authorization": f"Bearer {C.OPENROUTER_API_KEY}"},
                      timeout=30)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.text[:120]}"}
        d = r.json().get("data", {})
        limit, used = d.get("limit"), d.get("usage")
        out = {"key_limit": limit, "key_usage": used,
               "usage_daily": d.get("usage_daily")}
        rem = d.get("limit_remaining")
        if rem is None and limit is not None and used is not None:
            rem = limit - used
        if rem is not None:
            out["remaining"] = round(rem, 6)
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def summarize(path: str) -> dict:
    """Token/run totals from the run file. The harness logs usage, so unlike the
    July IRI batches these are exact rather than backed out from a credit delta."""
    n = pt = ct = err = trunc = 0
    tools = Counter()
    for line in open(path):
        if not line.strip():
            continue
        d = json.loads(line)
        n += 1
        pt += d.get("prompt_tokens") or 0
        ct += d.get("completion_tokens") or 0
        err += 1 if d.get("error") else 0
        trunc += 1 if d.get("truncated") else 0
        for t in d.get("tools_invoked") or []:
            tools[t] += 1
    return {"runs": n, "prompt_tokens": pt, "completion_tokens": ct,
            "errors": err, "truncated": trunc, "tool_calls": dict(tools)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="ONE model id; a list is rejected")
    ap.add_argument("--gold", default=os.path.join(HERE, "gold_all.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--conditions", nargs="*", default=C.CONDITIONS)
    ap.add_argument("--regimes", nargs="*", default=C.REGIMES)
    a = ap.parse_args()

    if "," in a.model or " " in a.model.strip():
        sys.exit("run_model.py takes exactly ONE model. Run it again for the next one.")
    if not C.OPENROUTER_API_KEY:
        sys.exit("set OPENROUTER_API_KEY")
    if not os.path.exists(a.gold):
        sys.exit(f"gold set not found: {a.gold} (build it with build_dl_gold.groovy)")

    out = a.out or os.path.join(HERE, f"runs_{a.model.split('/')[-1]}.jsonl")
    n_items = sum(1 for l in open(a.gold) if l.strip())
    n_runs = n_items * len(a.conditions) * len(a.regimes)

    before = credits()
    print(f"model      : {a.model}")
    print(f"gold       : {a.gold}  ({n_items} items)")
    print(f"conditions : {a.conditions}")
    print(f"planned    : {n_runs} runs")
    print(f"credits    : {before}")
    print("-" * 60)

    harness = _load_shared_harness()
    sys.argv = ["harness.py", "--gold", a.gold, "--out", out,
                "--models", a.model,
                "--conditions", *a.conditions,
                "--regimes", *a.regimes]
    asyncio.run(harness.main())

    after = credits()
    stats = summarize(out)
    spent = None
    if isinstance(before.get("remaining"), (int, float)) and isinstance(after.get("remaining"), (int, float)):
        spent = round(before["remaining"] - after["remaining"], 6)

    rec = {"ts": datetime.now(timezone.utc).isoformat(), "model": a.model,
           "gold": os.path.basename(a.gold), "conditions": a.conditions,
           "out": os.path.basename(out), "credits_before": before,
           "credits_after": after, "spent": spent, **stats}
    with open(CREDITS_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

    print("-" * 60)
    print(f"runs       : {stats['runs']} (errors {stats['errors']}, truncated {stats['truncated']})")
    print(f"tokens     : {stats['prompt_tokens']:,} in / {stats['completion_tokens']:,} out")
    print(f"tool calls : {stats['tool_calls']}")
    print(f"SPENT      : {spent if spent is not None else 'unknown'}")
    print(f"REMAINING  : {after.get('remaining')}")
    print(f"logged to  : {CREDITS_LOG}")
    print("\nSTOP. Report credits and wait for confirmation before the next model.")


if __name__ == "__main__":
    main()
