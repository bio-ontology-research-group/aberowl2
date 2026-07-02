"""Build the stratified gold set from AberOWL (so gold IRIs are grounded in the
ontology source, not invented).

Strata (see prompts.DIFFICULTY):
  L1_easy        : curated famous TERM NAMES -> resolved to their IRI via AberOWL
  L2_medium      : primary label of sampled classes (common ontologies)
  L3_hard        : an exact SYNONYM (!= primary label) of sampled classes
  L4_adversarial : (a) labels from OBSCURE ontologies, and
                   (b) NONEXISTENT near-miss terms (gold_iri=null -> expect abstain)

Output: gold.jsonl  {term, ontology, gold_iri|null, difficulty, note}

    python experiments/iri_hallucination/build_gold.py --out gold.jsonl --n 40

NOTE: this is a *candidate* generator — the gold MUST be reviewed (see README):
contamination (are L1/L2 memorized?), that L3 synonyms are unambiguous, and that
L4 nonexistent terms really don't resolve. Decisions still open (ontology set,
sizes, post-cutoff sourcing) — tune via the constants below.
"""
import argparse, json, re, random, collections
import httpx
import config as C

# --- knobs (open decisions — see README) ---
COMMON_ONTS = ["go", "chebi", "hp", "uberon", "cl", "mondo", "pato", "so"]
OBSCURE_ONTS = ["symp", "obi", "envo", "bfo", "iao", "ro"]   # smaller/less-famous of the indexed set
FAMOUS = [  # term NAMES only; IRIs resolved from AberOWL at build time
    ("apoptosis", "go"), ("glucose", "chebi"), ("neuron", "cl"), ("brain", "uberon"),
    ("seizure", "hp"), ("diabetes mellitus", "mondo"), ("membrane", "go"), ("water", "chebi"),
]
MINE_SEEDS = ["cell", "protein", "process", "regulation", "acid", "membrane", "binding"]

def api_get(path, **params):
    try:
        r = httpx.get(f"{C.ABEROWL_API}/{path}", params=params, timeout=25)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

def first(v): return (v[0] if isinstance(v, list) and v else v) or ""

MAX_TERM_LEN = 80   # skip IUPAC-monster synonyms and other pathological terms

def ont_of_iri(iri):
    """Real ontology of an IRI from its prefix (GO_x -> go), not the query."""
    frag = re.split(r"[#/]", (iri or "").rstrip("#/"))[-1]
    m = re.match(r"([A-Za-z]+)[_:]", frag)
    return m.group(1).lower() if m else None

def resolve(term, ont):
    """Canonical IRI for a term via AberOWL (grounded gold), or None."""
    d = api_get("resolve", query=term, ontologies=ont)
    r = (d.get("result") or [])
    return r[0].get("class") if r else None

def resolves_to(term, iri):
    """True iff an EXACT resolve of `term` (in the IRI's own ontology) returns
    `iri` — round-trip validation that `term` really is that class's label/synonym."""
    ont = ont_of_iri(iri)
    return bool(ont) and len(term) <= MAX_TERM_LEN and resolve(term, ont) == iri

def mine(ont, want):
    """Sample classes (class, label, synonyms) for an ontology via search_all."""
    seen, out = set(), []
    for seed in MINE_SEEDS:
        for c in (api_get("search_all", query=seed, ontologies=ont, size=50).get("result") or []):
            iri = c.get("class")
            if not iri or iri in seen: continue
            seen.add(iri)
            out.append({"iri": iri, "label": first(c.get("label")),
                        "synonyms": [s for s in (c.get("synonyms") or []) if s]})
        if len(out) >= want * 3: break
    random.shuffle(out); return out

def not_exists(term, ont):
    d = api_get("resolve", query=term, ontologies=ont)
    return not (d.get("result"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=40, help="items per stratum (approx)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(); random.seed(a.seed)
    items = []

    # L1 — famous names, IRI resolved from AberOWL
    for term, ont in FAMOUS:
        iri = resolve(term, ont)
        if iri: items.append(dict(term=term, ontology=ont, gold_iri=iri, difficulty="L1_easy", note="curated famous"))

    # L2 / L3 — mine common ontologies (ontology DERIVED from IRI, round-trip validated)
    per = a.n // len(COMMON_ONTS) + 1
    for ont in COMMON_ONTS:
        n2 = n3 = 0
        for c in mine(ont, per):
            oi = ont_of_iri(c["iri"])
            if c["label"] and n2 < per and resolves_to(c["label"], c["iri"]):
                items.append(dict(term=c["label"], ontology=oi, gold_iri=c["iri"], difficulty="L2_medium", note="primary label")); n2 += 1
            syn = next((s for s in c["synonyms"] if s.lower() != c["label"].lower()), None)
            if syn and n3 < per and resolves_to(syn, c["iri"]):
                items.append(dict(term=syn, ontology=oi, gold_iri=c["iri"], difficulty="L3_hard", note="exact synonym")); n3 += 1

    # L4a — obscure ontologies (labels unlikely memorized), same validation
    per = a.n // len(OBSCURE_ONTS) + 1
    for ont in OBSCURE_ONTS:
        n = 0
        for c in mine(ont, per):
            if n >= per: break
            if c["label"] and resolves_to(c["label"], c["iri"]):
                items.append(dict(term=c["label"], ontology=ont_of_iri(c["iri"]), gold_iri=c["iri"], difficulty="L4_adversarial", note="obscure ontology")); n += 1

    # L4b — plausible NONEXISTENT terms: take a multi-word label and swap its
    # last word for another class's last word (yields GO-style plausible terms),
    # keep only if it doesn't resolve. Subtler than a suffix the model can strip.
    pool = []
    for ont in COMMON_ONTS:
        for c in mine(ont, a.n):
            lbl = c["label"]
            if lbl and lbl.count(" ") >= 2 and len(lbl) <= MAX_TERM_LEN:
                pool.append((ont, lbl))
    random.shuffle(pool)
    tails = list({lbl.rsplit(" ", 1)[-1] for _, lbl in pool})
    made = 0
    for ont, lbl in pool:
        if made >= a.n: break
        head, last = lbl.rsplit(" ", 1)
        alts = [t for t in tails if t.lower() != last.lower()]
        if not alts: continue
        fake = f"{head} {random.choice(alts)}"
        if fake.lower() != lbl.lower() and not_exists(fake, ont):
            items.append(dict(term=fake, ontology=ont, gold_iri=None, difficulty="L4_adversarial", note="nonexistent (recombined)")); made += 1

    with open(a.out, "w") as f:
        for it in items: f.write(json.dumps(it) + "\n")
    by = collections.Counter(i["difficulty"] for i in items)
    print(f"wrote {len(items)} items to {a.out}: {dict(by)}")

if __name__ == "__main__":
    main()
