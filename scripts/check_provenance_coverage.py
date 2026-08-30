#!/usr/bin/env python3
"""
check_provenance_coverage.py — how many hosted ontologies have a resolvable
upstream download URL?

Why this exists
---------------
Every AberOWL 2 registry entry in production lacks `source_url`, so the update
pipeline never runs (`_check_and_update_all` returns early without one). Before
building anything to fix that (issue #95), we need to know how much of the
corpus intake could actually resolve — if only a third of it has a findable
upstream, the update story changes shape.

This answers that question **read-only**. It fetches the same two source
catalogues the intake code uses, matches them against the ontologies a
deployment actually serves, and reports the coverage. It writes nothing to
Redis, nothing to the registry, and nothing to any server.

It deliberately does NOT call POST /admin/sync_sources: that endpoint's upsert
(`_upsert_registry_from_source`) creates a registry entry for every ontology in
the source catalogues, hosted or not, which on production would add thousands of
worker-less entries to the same hash that serves /api/listOntologies.

Usage
-----
    python3 scripts/check_provenance_coverage.py
    python3 scripts/check_provenance_coverage.py --base-url http://localhost:8000
    python3 scripts/check_provenance_coverage.py --json --out results/provenance/coverage.json

    # BioPortal is skipped unless a key is supplied
    BIOPORTAL_API_KEY=... python3 scripts/check_provenance_coverage.py --with-bioportal

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "http://aber-owl.net"

# The intake code reads the YAML form; the JSON-LD form is the same catalogue
# and needs no third-party parser.
OBO_URL = "http://purl.obolibrary.org/meta/ontologies.jsonld"
BIOPORTAL_URL = "https://data.bioontology.org/ontologies"

TIMEOUT = 120


def fetch_json(url: str, timeout: int = TIMEOUT):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def hosted_ontologies(base_url: str) -> list[str]:
    """Ontology ids the deployment actually serves."""
    data = fetch_json(f"{base_url.rstrip('/')}/api/listOntologies")
    return [o["id"] for o in data.get("result", []) if o.get("id")]


def obo_purls() -> dict[str, str]:
    """id (lowercase) -> ontology_purl, for non-obsolete OBO Foundry entries.

    Mirrors the filtering in app/intake/obofoundry.py: skip is_obsolete, skip
    entries without a purl.
    """
    data = fetch_json(OBO_URL)
    out = {}
    for entry in data.get("ontologies", []):
        if entry.get("is_obsolete"):
            continue
        purl = entry.get("ontology_purl")
        oid = (entry.get("id") or "").strip().lower()
        if purl and oid:
            out[oid] = purl
    return out


def bioportal_acronyms(api_key: str) -> dict[str, str]:
    """acronym (lowercase) -> the ontology's BioPortal landing URL.

    Only the *listing* is fetched. Resolving each ontology's real download URL
    costs one request per ontology (that is what intake/bioportal.py does); for
    a coverage count the listing is enough to say whether BioPortal knows it.
    """
    url = f"{BIOPORTAL_URL}?{urllib.parse.urlencode({'apikey': api_key})}"
    data = fetch_json(url)
    out = {}
    for o in data:
        acr = (o.get("acronym") or "").strip().lower()
        if acr:
            out[acr] = o.get("links", {}).get("ui") or o.get("@id", "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help=f"deployment to read the hosted list from (default {DEFAULT_BASE_URL})")
    ap.add_argument("--with-bioportal", action="store_true",
                    help="also check BioPortal (needs BIOPORTAL_API_KEY)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--out", help="also write the JSON report here")
    ap.add_argument("--list-missing", action="store_true",
                    help="print every id with no upstream found")
    args = ap.parse_args()

    hosted = hosted_ontologies(args.base_url)
    hosted_l = {h.lower(): h for h in hosted}

    obo = obo_purls()
    bp: dict[str, str] = {}
    if args.with_bioportal:
        key = os.getenv("BIOPORTAL_API_KEY", "")
        if not key:
            print("--with-bioportal needs BIOPORTAL_API_KEY in the environment",
                  file=sys.stderr)
            return 2
        bp = bioportal_acronyms(key)

    in_obo, in_bp, missing = [], [], []
    for low, orig in sorted(hosted_l.items()):
        if low in obo:
            in_obo.append(orig)
        elif low in bp:
            in_bp.append(orig)
        else:
            missing.append(orig)

    total = len(hosted)
    covered = len(in_obo) + len(in_bp)
    report = {
        "base_url": args.base_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hosted": total,
        "obo_catalogue_size": len(obo),
        "bioportal_catalogue_size": len(bp) if args.with_bioportal else None,
        "resolvable_via_obo": len(in_obo),
        "resolvable_via_bioportal": len(in_bp),
        "unresolved": len(missing),
        "coverage_pct": round(100.0 * covered / total, 1) if total else 0.0,
        "unresolved_ids": missing,
    }

    if args.out:
        from pathlib import Path
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, indent=2) + "\n")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Provenance coverage for {args.base_url}\n")
        print(f"  hosted ontologies          {total:>6}")
        print(f"  OBO Foundry catalogue      {len(obo):>6}")
        if args.with_bioportal:
            print(f"  BioPortal catalogue        {len(bp):>6}")
        print()
        print(f"  resolvable via OBO Foundry {len(in_obo):>6}")
        if args.with_bioportal:
            print(f"  resolvable via BioPortal   {len(in_bp):>6}")
        print(f"  no upstream found          {len(missing):>6}")
        print()
        print(f"  coverage                   {report['coverage_pct']:>5}%")
        if args.list_missing and missing:
            print("\n  unresolved:")
            for m in missing:
                print(f"    {m}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
