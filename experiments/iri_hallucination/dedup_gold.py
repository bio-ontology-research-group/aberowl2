"""De-duplicate the gold set and extract the accidental replicate pairs.

gold.jsonl has 173 rows but only 162 unique (term, ontology) keys: 10 terms were
sampled into more than one difficulty stratum, so 11 rows are duplicates. Every
reported percentage is therefore weighted by an artefact (review point W11b).

The duplicates are not only a defect. Because the harness ran each row, the same
prompt was sent twice to the same model under the same condition, which is the
only repeat measurement in the study. Extract those pairs BEFORE de-duplicating:
they give a run-to-run determinism estimate at temperature 0, which the paper
otherwise cannot report (review point W7 asks for variance and there is none).

    python dedup_gold.py --gold gold.jsonl --runs runs_full.jsonl \
        --out gold_dedup.jsonl --replicates replicates.json
"""
import argparse, collections, json, re

ORDER = ["L1_easy", "L2_medium", "L3_hard", "L4_adversarial"]
_IRI = re.compile(r"https?://[^\s\"'<>]+")


def extract_iri(answer):
    if not answer or re.search(r"\bUNKNOWN\b", answer):
        return None
    m = _IRI.search(answer)
    return m.group(0).rstrip(".,);]") if m else None


def norm(iri):
    return (iri or "").strip().rstrip("/").replace("_", ":")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold.jsonl")
    ap.add_argument("--runs", default="runs_full.jsonl")
    ap.add_argument("--out", default="gold_dedup.jsonl")
    ap.add_argument("--replicates", default="replicates.json")
    a = ap.parse_args()

    gold = [json.loads(l) for l in open(a.gold) if l.strip()]
    runs = [json.loads(l) for l in open(a.runs) if l.strip()]

    # ---- replicate pairs: same model/regime/condition/term/ontology, run twice
    groups = collections.defaultdict(list)
    for r in runs:
        groups[(r["model"], r["regime"], r["condition"], r["term"], r.get("ontology"))].append(r)
    multi = {k: v for k, v in groups.items() if len(v) > 1}

    pairs = agree_iri = agree_corr = 0
    by_cond = collections.defaultdict(lambda: [0, 0, 0])
    for k, v in multi.items():
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                pairs += 1
                a_, b_ = v[i], v[j]
                ia, ib = extract_iri(a_.get("answer")), extract_iri(b_.get("answer"))
                same_iri = norm(ia) == norm(ib)
                gold_iri = a_.get("gold_iri")
                ca = (gold_iri is not None and norm(ia) == norm(gold_iri))
                cb = (gold_iri is not None and norm(ib) == norm(gold_iri))
                agree_iri += same_iri
                agree_corr += (ca == cb)
                st = by_cond[a_["condition"]]
                st[0] += 1; st[1] += same_iri; st[2] += (ca == cb)

    rep = {"identical_prompt_pairs": pairs,
           "exact_iri_agreement": round(100 * agree_iri / pairs, 1) if pairs else None,
           "correctness_agreement": round(100 * agree_corr / pairs, 1) if pairs else None,
           "by_condition": {c: {"pairs": s[0],
                                "exact_iri_agreement": round(100 * s[1] / s[0], 1),
                                "correctness_agreement": round(100 * s[2] / s[0], 1)}
                            for c, s in by_cond.items()},
           "note": ("Temperature 0 is not deterministic in this study's own data. "
                    "All pairs are WITHIN a single harness invocation, so they bound "
                    "run-to-run noise but not provider drift over time.")}
    json.dump(rep, open(a.replicates, "w"), indent=2)
    print(f"replicates: {pairs} identical-prompt pairs")
    print(f"  exact-IRI agreement {rep['exact_iri_agreement']}% | "
          f"correctness agreement {rep['correctness_agreement']}%")
    for c, s in sorted(rep["by_condition"].items()):
        print(f"    {c:10s} pairs={s['pairs']:4d} iri={s['exact_iri_agreement']:5.1f}% "
              f"correct={s['correctness_agreement']:5.1f}%")

    # ---- de-duplicate, keeping the EASIEST stratum per key
    best = {}
    for g in gold:
        k = (g["term"], g.get("ontology"))
        if k not in best or ORDER.index(g["difficulty"]) < ORDER.index(best[k]["difficulty"]):
            best[k] = g
    ded = list(best.values())
    with open(a.out, "w") as f:
        for g in ded:
            f.write(json.dumps(g) + "\n")

    strata = collections.Counter(g["difficulty"] for g in ded)
    real = [g for g in ded if g.get("gold_iri")]
    per_ont = collections.Counter(g.get("ontology") for g in real)
    assert sum(per_ont.values()) == len(real), "per-ontology breakdown must sum to the real-class count"
    assert len(ded) == len({(g["term"], g.get("ontology")) for g in ded}), "keys must be unique"
    print(f"\nde-duplicated: {len(gold)} -> {len(ded)} items "
          f"({len(real)} real, {len(ded) - len(real)} nonexistent)")
    print("  strata:", dict(strata))
    print("  per-ontology (real only):", dict(per_ont.most_common()))


if __name__ == "__main__":
    main()
