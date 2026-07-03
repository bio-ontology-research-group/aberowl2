"""Score harness runs.

Per response, classify the produced IRI against gold:
  correct      : extracted IRI == gold IRI
  valid_wrong  : IRI exists in the ontology but != gold (mislinking)
  hallucinated : IRI does NOT exist (fabricated); for nonexistent-term items
                 (gold=null) ANY produced IRI counts as hallucinated
  abstained    : UNKNOWN / no IRI produced

Existence is checked with AberOWL's reasoner endpoint (getClass), independent of
find_iri's ES path. Checks are batched in PARALLEL (thousands of unique wrong
IRIs would time out sequentially).

Reports, per (model, regime, condition[, difficulty]):
  accuracy, hallucination_rate (of all), halluc_among_answered, abstention_rate,
  used_find_iri (fraction of responses that invoked the grounding tool).

    python score.py --runs runs_full.jsonl [--by-difficulty]
"""
import argparse, json, re, collections, os, concurrent.futures as cf
import httpx
import config as C

_CACHE_F = os.path.join(os.path.dirname(__file__) or ".", "iri_exists_cache.json")

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

def _exists_one(iri):
    ont = _ontology_of(iri)
    if not ont: return (iri, False)
    for _ in range(3):                       # retry: getClass is flaky under load
        try:
            r = httpx.get(f"{C.ABEROWL_API}/getClass", params={"query": iri, "ontology": ont}, timeout=20)
            if r.status_code == 200:
                d = r.json()
                return (iri, bool(d) and not d.get("error") and (d.get("class") or d.get("label")))
        except Exception:
            pass
    return (iri, False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--by-difficulty", action="store_true")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.runs) if l.strip()]

    # pass 1: label what needs no network; collect the IRIs that do
    to_check = set()
    for r in rows:
        gold, got = r.get("gold_iri"), extract_iri(r.get("answer", ""))
        r["_got"] = got
        if got is None:            r["_lab"] = "abstained"
        elif gold is None:         r["_lab"] = "hallucinated"     # nonexistent term
        elif norm(got) == norm(gold): r["_lab"] = "correct"
        else:                      r["_lab"] = "?"; to_check.add(got)

    # batch existence checks (cached to disk; moderate concurrency — getClass is flaky under load)
    exist = json.load(open(_CACHE_F)) if os.path.exists(_CACHE_F) else {}
    need = [i for i in to_check if i not in exist]
    if need:
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for iri, ok in ex.map(_exists_one, need):
                exist[iri] = bool(ok)
        json.dump(exist, open(_CACHE_F, "w"))
    for r in rows:
        if r["_lab"] == "?":
            r["_lab"] = "valid_wrong" if exist.get(r["_got"]) else "hallucinated"

    keyf = (lambda r: (r["model"], r["regime"], r["condition"], r.get("difficulty", "-"))) if a.by_difficulty \
           else (lambda r: (r["model"], r["regime"], r["condition"]))
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[keyf(r)].append(r)

    hdr = ("model", "regime", "cond") + (("diff",) if a.by_difficulty else ())
    print(f"{'  '.join(hdr):55} | n   acc%  halluc%  h/ans%  abst%  usedTool%")
    for k in sorted(buckets):
        b = buckets[k]; n = len(b)
        c = collections.Counter(r["_lab"] for r in b)
        answered = n - c["abstained"]
        acc = 100*c["correct"]/n
        hall = 100*c["hallucinated"]/n
        hans = 100*c["hallucinated"]/answered if answered else 0
        abst = 100*c["abstained"]/n
        used = 100*sum(1 for r in b if "find_iri" in (r.get("tools_invoked") or []))/n
        print(f"{'  '.join(str(x) for x in k):55} | {n:<3} {acc:5.0f} {hall:7.0f} {hans:6.0f} {abst:5.0f}  {used:7.0f}")

if __name__ == "__main__":
    main()
