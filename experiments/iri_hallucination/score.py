"""Score harness runs.

Per response, classify the produced IRI against gold:
  correct      : extracted IRI == gold IRI
  valid_wrong  : IRI exists in the ontology but != gold (mislinking)
  hallucinated : IRI does NOT exist (fabricated)
  abstained    : UNKNOWN / no IRI produced
For L4-nonexistent items (gold_iri is null) the correct behaviour is abstain;
any produced IRI is a hallucination.

Existence is checked with AberOWL's reasoner endpoint (getClass), independent of
find_iri's ES path, to avoid grading find_iri with itself.

Reports, per (model, regime, condition[, difficulty]):
  accuracy, hallucination_rate (of all), halluc_among_answered, abstention_rate,
  and used_find_iri (fraction of responses that invoked the grounding tool).

    python experiments/iri_hallucination/score.py --runs runs.jsonl
"""
import argparse, json, re, sys, collections, functools
import httpx
import config as C

_IRI = re.compile(r"https?://[^\s\"'<>]+")

def extract_iri(answer: str):
    if not answer: return None
    if re.search(r"\bUNKNOWN\b", answer): return None
    m = _IRI.search(answer)
    return m.group(0).rstrip(".,);]") if m else None

def norm(iri): return (iri or "").strip().rstrip("/").replace("_", ":")

def _ontology_of(iri):
    frag = re.split(r"[#/]", iri.rstrip("#/"))[-1]
    m = re.match(r"([A-Za-z]+)[_:]", frag)
    return m.group(1).lower() if m else None

@functools.lru_cache(maxsize=4096)
def iri_exists(iri: str) -> bool:
    ont = _ontology_of(iri)
    if not ont: return False
    try:
        r = httpx.get(f"{C.ABEROWL_API}/getClass", params={"query": iri, "ontology": ont}, timeout=20)
        if r.status_code != 200: return False
        d = r.json()
        return bool(d) and not d.get("error") and (d.get("class") or d.get("label"))
    except Exception:
        return False

def classify(res):
    gold = res.get("gold_iri")
    got = extract_iri(res.get("answer", ""))
    if got is None:
        return "abstained"
    if gold and norm(got) == norm(gold):
        return "correct"
    return "valid_wrong" if iri_exists(got) else "hallucinated"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--by-difficulty", action="store_true")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.runs) if l.strip()]

    keyf = (lambda r: (r["model"], r["regime"], r["condition"], r.get("difficulty", "-"))) if a.by_difficulty \
           else (lambda r: (r["model"], r["regime"], r["condition"]))
    buckets = collections.defaultdict(list)
    for r in rows:
        r["_label"] = classify(r)
        buckets[keyf(r)].append(r)

    hdr = ("model", "regime", "cond") + (("diff",) if a.by_difficulty else ())
    print(f"{'  '.join(hdr):55} | n   acc%  halluc%  h/ans%  abst%  usedTool%")
    for k in sorted(buckets):
        b = buckets[k]; n = len(b)
        c = collections.Counter(r["_label"] for r in b)
        answered = n - c["abstained"]
        acc = 100*c["correct"]/n
        hall = 100*c["hallucinated"]/n
        hans = 100*c["hallucinated"]/answered if answered else 0
        abst = 100*c["abstained"]/n
        used = 100*sum(1 for r in b if "find_iri" in (r.get("tools_invoked") or []))/n
        print(f"{'  '.join(str(x) for x in k):55} | {n:<3} {acc:5.0f} {hall:7.0f} {hans:6.0f} {abst:5.0f}  {used:7.0f}")

if __name__ == "__main__":
    main()
