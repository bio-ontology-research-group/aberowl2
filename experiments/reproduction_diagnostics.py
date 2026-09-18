"""Offline diagnostics derived from saved calls, using the published DL scorer.

Best-call selection maximizes strict set F1 (first call wins ties). Log-cap
counts indicate potentially clipped recorded text, not truncation seen by the
model. Paired p-values are exact, two-sided and unadjusted.
"""
import importlib.util
import math
from collections import Counter
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    "reproduction_score_dl", Path(__file__).parent / "dl_reasoning/score_dl.py")
_dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dl)


def _key(row):
    return row["model"], row["condition"], row["term"], row.get("ontology")


def _index(rows, key):
    result = {}
    for row in rows:
        k = key(row)
        if k in result:
            raise ValueError(f"Duplicate diagnostic input: {k}")
        result[k] = row
    return result


def reasoning_diagnostics(raw_rows, gold_rows, scored_rows):
    """Return per-run first/last/best audit, unhinted totals and log-cap counts."""
    gold = _index(gold_rows, lambda r: (r["term"], r.get("ontology")))
    scored = _index(scored_rows, _key)
    raw = _index(raw_rows, _key)
    if raw.keys() != scored.keys():
        raise ValueError("Raw and scored reasoning runs do not align")
    audit, failures = [], Counter()
    call_count = capped = 0
    for key, row in raw.items():
        calls = [c for c in row.get("tool_calls", [])
                 if c["tool"] == "run_dl_query" and not c.get("blocked")]
        call_count += len(calls)
        capped += sum(len(c.get("result", "")) >= 6000 for c in calls)
        if row["condition"] != "dlquery":
            continue
        g = gold[(row["term"], row.get("ontology"))]
        target, anchors = set(g["gold_iris"]), set(g.get("anchor_iris") or [])
        pred = _dl.parse_iris(row.get("answer", ""))
        s = scored[key]
        item = {k: row.get(k) for k in ("model", "condition", "term", "ontology")}
        item.update(task=g["task"], calls=len(calls), exact_set=pred == target)
        if calls:
            sets = [_dl.parse_tool_result(c.get("result", "")) for c in calls]
            best_index = max(range(len(sets)), key=lambda i: _dl.prf(sets[i], target)[2])
            best = sets[best_index]
            item.update(
                best_call_index=best_index,
                first_formulation_strict=sets[0] == target,
                last_formulation_strict=sets[-1] == target,
                best_formulation_strict=best == target,
                best_formulation_normalized=best - anchors == target - anchors,
                relay_strict=pred == best,
                relay_normalized=pred - anchors == best - anchors,
            )
            for field, expected in (
                ("formulation_ok", item["best_formulation_normalized"]),
                ("formulation_ok_strict", item["best_formulation_strict"]),
                ("relay_ok", item["relay_normalized"]),
                ("relay_ok_strict", item["relay_strict"]),
            ):
                if s[field] != expected:
                    raise ValueError(f"Scorer disagreement for {key}: {field}")
        if not item["exact_set"]:
            if not calls:
                failure = "no_adoption"
            elif not item["best_formulation_normalized"]:
                failure = "formulation"
            elif pred - anchors == target - anchors:
                failure = "anchor_inclusion"
            else:
                failure = "relay"
            item["failure"] = failure
            failures[failure] += 1
        audit.append(item)
    fields = ("first_formulation_strict", "last_formulation_strict",
              "best_formulation_strict", "best_formulation_normalized",
              "relay_strict", "relay_normalized")
    return {
        "unhinted": {"runs": len(audit), "adopted": sum(r["calls"] > 0 for r in audit),
                     **{f: sum(r.get(f, False) for r in audit) for f in fields},
                     "failures": dict(sorted(failures.items()))},
        "recorded_reasoning_calls": {"calls": call_count, "at_log_cap": capped,
                                     "log_cap_characters": 6000},
        "unhinted_per_run": audit,
    }


def _paired(rows, before, after, success):
    pairs = _index(rows, lambda r: (r["condition"], r["term"], r.get("ontology")))
    keys = {(r["term"], r.get("ontology")) for r in rows}
    gains = losses = both_success = both_failure = 0
    for term, ontology in keys:
        if (before, term, ontology) not in pairs or (after, term, ontology) not in pairs:
            raise ValueError(f"Missing paired condition for {term!r}, {ontology!r}")
        a, b = (bool(success(pairs[c, term, ontology])) for c in (before, after))
        gains += not a and b
        losses += a and not b
        both_success += a and b
        both_failure += not a and not b
    n = gains + losses
    p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gains, losses) + 1)) / 2**n)
    return {"before": before, "after": after, "pairs": len(keys),
            "gains": gains, "losses": losses, "both_success": both_success,
            "both_failure": both_failure, "p_unadjusted": p}


def paired_contrasts(grounding_scored, dl_scored):
    """Per-model negative declines and T2 exact-set hint contrasts.

    Gains mean success only in the `after` condition; losses mean success only
    in `before`. Both condition inputs must contain every paired item exactly once.
    """
    output = {"grounding_negative_abstain": {}, "reasoning_T2_hint": {}}
    for name, rows, before, after, success in (
        ("grounding_negative_abstain",
         [r for r in grounding_scored if r["regime"] == "abstain"
          and r.get("gold_iri") is None and r["condition"] in ("none", "find_iri")],
         "none", "find_iri", lambda r: r["_lab"] == "abstained"),
        ("reasoning_T2_hint",
         [r for r in dl_scored if r["task"] == "T2"
          and r["condition"] in ("dlquery", "dlquery_hint")],
         "dlquery", "dlquery_hint", lambda r: r["exact_set"]),
    ):
        for model in sorted({r["model"] for r in rows}):
            output[name][model] = _paired(
                [r for r in rows if r["model"] == model], before, after, success)
    return output
