"""Score the DL-reasoning experiment, with the four-stage failure decomposition.

End-to-end set F1 alone cannot say WHY a run failed. With the tool call's `args`
logged verbatim, every failure in the reasoning arms attributes to a stage:

  adoption     did the model call run_dl_query at all?
  formulation  did the expression it built return the gold set?
  service      did AberOWL answer the model's own query correctly?
  relay        did the final answer match what the tool returned?

That decomposition is the finding. "Models call the tool X% of the time, build a
correct expression Y% of the time, and relay it faithfully Z%" says something about
agent-facing reasoning services; a single accuracy number does not.

Fabrication is checked against the class universe dumped by build_dl_gold.groovy,
so it needs no network oracle -- avoiding the silent-False resolver bugs that made
the IRI experiment's hallucination column wrong.

Usage:
    python score_dl.py --gold gold_all.jsonl --runs 'runs_*.jsonl' \
        --classes classes_go.txt classes_cl.txt classes_so.txt
"""
import argparse
import glob
import json
import math
import re
from collections import defaultdict

IRI_RE = re.compile(r"https?://[^\s,;\"'<>\]\)]+")


def parse_iris(text: str) -> set:
    """IRIs from a model answer or a tool result. Tolerant on purpose: the model is
    told one `IRI: <iri>` per line, and deviating from that is a formatting slip,
    not a reasoning error, so we do not punish it here."""
    if not text:
        return set()
    if text.strip().upper() == "NONE":
        return set()
    return {m.rstrip(".,;)") for m in IRI_RE.findall(text)}


def prf(pred: set, gold: set):
    if not pred and not gold:
        return 1.0, 1.0, 1.0
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def wilson(k: int, n: int, z: float = 1.96):
    """95% CI on a proportion. W7 in the review: the paper reported none."""
    if n == 0:
        return 0.0, 0.0, 0.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return ph, max(0.0, c - h), min(1.0, c + h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--runs", nargs="+", required=True, help="run files or globs")
    ap.add_argument("--classes", nargs="*", default=[], help="class-universe files")
    ap.add_argument("--out", default=None, help="write per-run scored JSONL here")
    a = ap.parse_args()

    gold = {}
    for line in open(a.gold):
        if not line.strip():
            continue
        g = json.loads(line)
        gold[(g["term"], g.get("ontology"))] = g

    universe = set()
    for f in a.classes:
        universe |= {l.strip() for l in open(f) if l.strip()}

    paths = []
    for r in a.runs:
        paths.extend(glob.glob(r) if any(c in r for c in "*?[") else [r])

    scored = []
    for path in paths:
        for line in open(path):
            if not line.strip():
                continue
            d = json.loads(line)
            g = gold.get((d["term"], d.get("ontology")))
            if g is None:
                continue
            gset = set(g["gold_iris"])
            pred = parse_iris(d.get("answer", ""))
            p, r, f = prf(pred, gset)

            row = {
                "model": d["model"], "condition": d["condition"], "task": g["task"],
                "ontology": g.get("ontology"), "term": d["term"],
                "n_gold": len(gset), "n_pred": len(pred),
                "precision": p, "recall": r, "f1": f,
                "exact_set": pred == gset,
                # "NONE" conflates "the answer set is empty" with "I do not know".
                # No gold item has an empty answer, so it is always wrong -- but it is a
                # DIFFERENT failure from returning wrong IRIs, and the paper should say so.
                "asserted_empty": (d.get("answer") or "").strip().upper() == "NONE",
                "error": d.get("error"), "truncated": bool(d.get("truncated")),
            }
            if universe:
                off = pred - universe
                row["n_fabricated"] = len(off)
                row["fabrication_rate"] = len(off) / len(pred) if pred else 0.0

            # ---- decomposition (reasoning arms only) ----
            calls = [t for t in (d.get("tool_calls") or []) if t.get("tool") == "run_dl_query"]
            row["adopted"] = bool(calls)
            if calls:
                # Best tool result the model actually received.
                best, best_f = set(), -1.0
                for c in calls:
                    s = parse_iris(c.get("result", ""))
                    _, _, cf = prf(s, gset)
                    if cf > best_f:
                        best, best_f = s, cf
                row["tool_f1"] = best_f
                # Formulation succeeded if the expression the model built returned gold.
                row["formulation_ok"] = best == gset
                # Relay succeeded if the final answer preserved what the tool returned.
                row["relay_ok"] = pred == best
                row["queries"] = [c.get("args", {}).get("query") for c in calls]
            scored.append(row)

    if a.out:
        with open(a.out, "w") as fh:
            for s in scored:
                fh.write(json.dumps(s) + "\n")

    # ------------------------------------------------------------ aggregate
    def agg(rows):
        n = len(rows)
        if not n:
            return None
        ex = sum(1 for x in rows if x["exact_set"])
        ph, lo, hi = wilson(ex, n)
        o = {"n": n, "f1": sum(x["f1"] for x in rows) / n,
             "precision": sum(x["precision"] for x in rows) / n,
             "recall": sum(x["recall"] for x in rows) / n,
             "exact": ph, "exact_lo": lo, "exact_hi": hi,
             "truncated": sum(1 for x in rows if x["truncated"]),
             "errors": sum(1 for x in rows if x["error"])}
        o["asserted_empty"] = sum(1 for x in rows if x.get("asserted_empty")) / n
        o["attempted"] = sum(1 for x in rows if x["n_pred"]) / n
        if any("n_fabricated" in x for x in rows):
            o["fabricated_runs"] = sum(1 for x in rows if x.get("n_fabricated"))
            att = [x for x in rows if x["n_pred"]]
            if att:
                o["fab_rate_of_attempts"] = sum(x.get("fabrication_rate", 0) for x in att) / len(att)
        dl = [x for x in rows if "formulation_ok" in x]
        o["adopted"] = sum(1 for x in rows if x.get("adopted")) / n
        if dl:
            o["formulation_ok"] = sum(1 for x in dl if x["formulation_ok"]) / len(dl)
            o["relay_ok"] = sum(1 for x in dl if x["relay_ok"]) / len(dl)
        return o

    by = defaultdict(list)
    for s in scored:
        by[(s["model"], s["condition"], s["task"])].append(s)

    print(f"{'model':22s} {'condition':13s} {'task':4s} {'n':>4s} {'F1':>6s} "
          f"{'exact':>6s} {'95% CI':>13s} {'att':>5s} {'NONE':>5s} {'fab':>5s} "
          f"{'adopt':>6s} {'form':>6s} {'relay':>6s}")
    print("-" * 124)
    for k in sorted(by):
        m, c, t = k
        s = agg(by[k])
        ci = f"[{s['exact_lo']*100:.1f},{s['exact_hi']*100:.1f}]"
        form = f"{s['formulation_ok']*100:5.1f}" if "formulation_ok" in s else "    -"
        rel = f"{s['relay_ok']*100:5.1f}" if "relay_ok" in s else "    -"
        fab = f"{s.get('fab_rate_of_attempts', 0)*100:4.1f}"
        print(f"{m.split('/')[-1]:22s} {c:13s} {t:4s} {s['n']:4d} {s['f1']*100:6.1f} "
              f"{s['exact']*100:6.1f} {ci:>13s} {s['attempted']*100:5.1f} "
              f"{s['asserted_empty']*100:5.1f} {fab:>5s} "
              f"{s['adopted']*100:5.1f} {form:>6s} {rel:>6s}")

    if not universe:
        print("\nNOTE: no --classes given, so fabrication was NOT measured.")


if __name__ == "__main__":
    main()
