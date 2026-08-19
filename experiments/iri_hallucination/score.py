"""Score harness runs against a committed existence map.

Per response, classify the produced IRI against gold:
  correct      : extracted IRI == gold IRI
  valid_wrong  : the IRI EXISTS but is not the gold class (mislinking)
  hallucinated : the IRI does not exist; for nonexistent-term items (gold=null)
                 any produced IRI counts as hallucinated
  abstained    : UNKNOWN / no IRI produced
  unknown      : existence could not be checked -- EXCLUDED, never assumed absent

What changed, and why the published numbers move:

  - Existence now comes from a committed map (build_exists_map.py) rather than a
    live check inside scoring, so the same runs always score the same offline.
    The previous check had three silent-False paths (302 not followed, 429 under
    8-way concurrency, bare except), giving a 77.2% false-negative rate and
    inflating hallucination roughly two-fold.
  - A failed check is now `unknown` and excluded, not counted as a fabrication.
  - valid_wrong is REPORTED. It was computed before but never printed, which is
    what let the manuscript's claim about mislinking go unchecked (W3).
  - Optional --gold restricts scoring to a de-duplicated gold set (W11b), and
    --by-stratum splits real classes from constructed-nonexistent terms (W10).
  - Proportions carry 95% Wilson intervals (W7).

    python score.py --runs runs_full.jsonl [--gold gold_dedup.jsonl]
                    [--by-difficulty] [--by-stratum]
"""
import argparse, collections, json, math, os, re

_MAP_F = os.path.join(os.path.dirname(__file__) or ".", "iri_exists_resolved.json")
_IRI = re.compile(r"https?://[^\s\"'<>]+")


def extract_iri(answer):
    if not answer:
        return None
    if re.search(r"\bUNKNOWN\b", answer):
        return None
    m = _IRI.search(answer)
    return m.group(0).rstrip(".,);]") if m else None


def norm(iri):
    return (iri or "").strip().rstrip("/").replace("_", ":")


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0, 0.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return 100 * ph, 100 * max(0.0, c - h), 100 * min(1.0, c + h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--gold", help="restrict to this gold set (e.g. the de-duplicated one)")
    ap.add_argument("--map", default=_MAP_F)
    ap.add_argument("--by-difficulty", action="store_true")
    ap.add_argument("--by-stratum", action="store_true",
                    help="split real classes from constructed-nonexistent terms")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.runs) if l.strip()]

    if a.gold:
        keep = {(g["term"], g.get("ontology"))
                for g in (json.loads(l) for l in open(a.gold) if l.strip())}
        before = len(rows)
        rows = [r for r in rows if (r["term"], r.get("ontology")) in keep]
        print(f"gold filter {os.path.basename(a.gold)}: {before} -> {len(rows)} runs "
              f"({len(keep)} gold items)")

    blob = json.load(open(a.map))
    exist = blob.get("map", blob)
    prov = blob.get("_provenance")
    if prov:
        print(f"existence map: {prov.get('n')} IRIs resolved {prov.get('resolved_utc')} "
              f"({prov.get('exists')} exist, {prov.get('absent')} absent, "
              f"{prov.get('unresolved')} unresolved) via {', '.join(prov.get('hosts', []))}")

    missing = 0
    for r in rows:
        gold, got = r.get("gold_iri"), extract_iri(r.get("answer", ""))
        r["_got"] = got
        if got is None:
            r["_lab"] = "abstained"
        elif gold is None:
            r["_lab"] = "hallucinated"          # term names no class: any IRI is fabricated
        elif norm(got) == norm(gold):
            r["_lab"] = "correct"
        else:
            e = exist.get(got, "__absent__")
            if e is True:
                r["_lab"] = "valid_wrong"
            elif e is False:
                r["_lab"] = "hallucinated"
            else:
                r["_lab"] = "unknown"           # unresolved, or not in the map
                if e == "__absent__":
                    missing += 1
    if missing:
        print(f"WARNING: {missing} produced IRIs are absent from the map; "
              f"rebuild it with build_exists_map.py --runs {a.runs}")

    keyf = (lambda r: (r["model"], r["regime"], r["condition"], r.get("difficulty", "-"))) \
        if a.by_difficulty else (lambda r: (r["model"], r["regime"], r["condition"]))
    if a.by_stratum:
        base = keyf
        keyf = lambda r: base(r) + ("real" if r.get("gold_iri") else "null-gold",)

    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[keyf(r)].append(r)

    print(f"\n{'model / regime / cond':52} |  n   acc%[95% CI]      halluc%[95% CI]   "
          f"vwrong%  abst%  unk")
    for k in sorted(buckets):
        b = buckets[k]
        n = len(b)
        c = collections.Counter(r["_lab"] for r in b)
        scored = n - c["unknown"]              # unknown is excluded, not assumed
        acc, alo, ahi = wilson(c["correct"], n)
        hal, hlo, hhi = wilson(c["hallucinated"], scored)
        vw = 100 * c["valid_wrong"] / scored if scored else 0
        ab = 100 * c["abstained"] / n
        label = " / ".join(str(x) for x in k)
        print(f"{label:52} | {n:<3} {acc:5.1f}[{alo:4.1f},{ahi:4.1f}]  "
              f"{hal:5.1f}[{hlo:4.1f},{hhi:4.1f}]  {vw:6.1f} {ab:6.1f} {c['unknown']:4d}")


if __name__ == "__main__":
    main()
