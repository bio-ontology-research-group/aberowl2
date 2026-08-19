"""Resolve IRI existence into a committed, deterministic map.

Replaces the existence check embedded in score.py, which had three silent-False
paths and so reported "check failed" as "identifier does not exist":

  1. httpx.get without follow_redirects: beta answers getClass with a 302 to
     phenomebrowser.net, and `if r.status_code == 200` scored that as absent.
  2. HTTP 429 under 8-way concurrency with no backoff, likewise scored absent.
  3. a bare `except: pass` falling through to False.

Measured false-negative rate of the shipped cache: 234 of 303 (77.2%). The
corrected map moves Table 1's hallucination column from 45/40/59/41/64 to
24/25/31/23/35, so this is not a rounding issue.

Distinguishes three states, which is the point:
    true  -> checked, the class exists
    false -> checked, definitively absent (HTTP 404)
    null  -> the check FAILED (429/5xx/timeout); scoring must exclude, not assume

Queries both the production and beta hosts and merges: a class exists if either
host resolves it. Writes the map plus provenance (hosts, date, counts).

    python build_exists_map.py --runs runs_full.jsonl --out iri_exists_resolved.json
"""
import argparse, collections, json, os, re, sys, time
import concurrent.futures as cf

import httpx

_IRI = re.compile(r"https?://[^\s\"'<>]+")

HOSTS = ["http://aber-owl.net/api", "https://beta.aber-owl.net/api"]


def extract_iri(answer):
    if not answer:
        return None
    if re.search(r"\bUNKNOWN\b", answer):
        return None
    m = _IRI.search(answer)
    return m.group(0).rstrip(".,);]") if m else None


def ontology_of(iri):
    frag = re.split(r"[#/]", iri.rstrip("#/"))[-1]
    m = re.match(r"([A-Za-z]+)[_:]", frag)
    return m.group(1).lower() if m else None


def check(iri, host, tries=5):
    """True / False / None. None means the check failed and must not be scored."""
    ont = ontology_of(iri)
    if not ont:
        # No parseable prefix: cannot name an ontology, so it cannot resolve.
        return False
    last_failed = False
    for attempt in range(tries):
        try:
            r = httpx.get(f"{host}/getClass", params={"query": iri, "ontology": ont},
                          timeout=30, follow_redirects=True)
        except Exception:
            last_failed = True
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                return None
            return bool(d) and not d.get("error") and bool(d.get("class") or d.get("label"))
        if r.status_code == 404:
            return False                      # definitively absent
        last_failed = True                    # 429 / 5xx: retry with backoff
        time.sleep(1.5 * (attempt + 1))
    return None if last_failed else False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", default="iri_exists_resolved.json")
    ap.add_argument("--workers", type=int, default=4,
                    help="keep low; 8 with no backoff is what produced the 429 storm")
    a = ap.parse_args()

    cands = set()
    for f in a.runs:
        for line in open(f):
            if not line.strip():
                continue
            r = json.loads(line)
            got = extract_iri(r.get("answer", ""))
            gold = r.get("gold_iri")
            # Only IRIs that are neither absent nor equal to gold need checking.
            if got and (gold is None or got.strip().rstrip("/").replace("_", ":")
                        != (gold or "").strip().rstrip("/").replace("_", ":")):
                cands.add(got)
    cands = sorted(cands)
    print(f"{len(cands)} distinct IRIs to resolve across {len(HOSTS)} hosts")

    per_host = {}
    for host in HOSTS:
        res = {}
        with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(check, i, host): i for i in cands}
            for n, fu in enumerate(cf.as_completed(futs), 1):
                res[futs[fu]] = fu.result()
                if n % 100 == 0:
                    print(f"  {host}: {n}/{len(cands)}")
        c = collections.Counter("true" if v is True else "false" if v is False else "null"
                                for v in res.values())
        print(f"  {host}: {dict(c)}")
        per_host[host] = res

    merged, unresolved = {}, 0
    for i in cands:
        vals = [per_host[h].get(i) for h in HOSTS]
        if any(v is True for v in vals):
            merged[i] = True
        elif all(v is False for v in vals):
            merged[i] = False
        else:
            merged[i] = None            # at least one check failed, none confirmed
            unresolved += 1

    exists = sum(1 for v in merged.values() if v is True)
    absent = sum(1 for v in merged.values() if v is False)
    out = {"_provenance": {"hosts": HOSTS, "resolved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "n": len(merged), "exists": exists, "absent": absent,
                           "unresolved": unresolved,
                           "note": "null = check failed; exclude from hallucination scoring"},
           "map": merged}
    json.dump(out, open(a.out, "w"), indent=0, sort_keys=True)
    print(f"wrote {a.out}: {exists} exist, {absent} absent, {unresolved} unresolved")


if __name__ == "__main__":
    main()
