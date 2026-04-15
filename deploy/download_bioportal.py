#!/usr/bin/env python3
"""
Download OWL files from BioPortal using the REST API.

Usage:
    python3 deploy/download_bioportal.py /data/aberowl/ontologies
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError

API_KEY = "24e0413e-54e0-11e0-9d7b-005056aa3316"
API_BASE = "https://data.bioontology.org"

# BioPortal ontologies to download (from beta_ontologies.json, minus restricted ones)
BP_ONTOLOGIES = [
    "MESH", "NCIT", "RADLEX", "FMA", "EFO", "EDAM", "SIO",
    "MEDDRA", "BAO", "CLO", "NIFSTD",
    "OMIM", "ORPHANET",
    "CRISP", "BIOMODELS", "HGNC",
    "PDQ", "CTCAE", "ICF", "BAO",
    "CHEAR", "MIRNAO", "KISAO", "TEDDY",
    "GFO", "PROVO",
    # Additional well-known BioPortal ontologies not in OBO
    "SNOMED_BODY", "ONTOMA", "RXNO",
]

# Ontologies already downloaded via OBO
ALREADY_HAVE = set()


def get_download_url(acronym: str) -> str:
    """Get the OWL download URL for a BioPortal ontology."""
    url = f"{API_BASE}/ontologies/{acronym}/download?apikey={API_KEY}"
    # Just return the download URL directly - BioPortal redirects
    return url


def get_ontology_info(acronym: str) -> dict:
    """Get ontology metadata from BioPortal."""
    try:
        req = Request(
            f"{API_BASE}/ontologies/{acronym}?apikey={API_KEY}",
            headers={"Accept": "application/json"},
        )
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  Could not fetch info for {acronym}: {e}")
        return None


def download_one(acronym: str, dest_dir: Path) -> dict:
    """Download a single BioPortal ontology."""
    ont_id = acronym.lower()
    ont_dir = dest_dir / ont_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    owl_path = ont_dir / f"{ont_id}.owl"

    if owl_path.exists() and owl_path.stat().st_size > 1000:
        return {"id": ont_id, "acronym": acronym, "status": "exists", "size": owl_path.stat().st_size}

    download_url = get_download_url(acronym)
    if not download_url:
        return {"id": ont_id, "acronym": acronym, "status": "no_url"}

    try:
        result = subprocess.run(
            [
                "curl", "-fSL",
                "--max-time", "600",
                "-H", f"Authorization: apikey token={API_KEY}",
                "-o", str(owl_path),
                download_url,
            ],
            capture_output=True, text=True, timeout=620,
        )
        if result.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > 100:
            return {"id": ont_id, "acronym": acronym, "status": "ok", "size": owl_path.stat().st_size}
        else:
            owl_path.unlink(missing_ok=True)
            return {"id": ont_id, "acronym": acronym, "status": "failed", "error": result.stderr[:200]}
    except Exception as e:
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "acronym": acronym, "status": "error", "error": str(e)[:200]}


def main():
    dest_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/aberowl/ontologies")

    # Check what we already have
    existing = set()
    for d in dest_dir.iterdir():
        if d.is_dir():
            owl = d / f"{d.name}.owl"
            if owl.exists() and owl.stat().st_size > 1000:
                existing.add(d.name)

    print(f"Already have {len(existing)} ontologies: {sorted(existing)[:10]}...")
    print(f"Downloading up to {len(BP_ONTOLOGIES)} BioPortal ontologies")
    print("=" * 60)

    results = {"ok": [], "exists": [], "failed": [], "skipped": []}

    for acronym in BP_ONTOLOGIES:
        ont_id = acronym.lower()
        if ont_id in existing:
            print(f"  [exists] {acronym:15s}")
            results["exists"].append(acronym)
            continue

        print(f"  [down  ] {acronym:15s} ...", end="", flush=True)
        r = download_one(acronym, dest_dir)
        if r["status"] in ("ok", "exists"):
            size_mb = r.get("size", 0) / 1024 / 1024
            print(f" {size_mb:.1f} MB")
            results["ok"].append(acronym)
        elif r["status"] == "no_url":
            print(" skipped (no URL)")
            results["skipped"].append(acronym)
        else:
            print(f" FAILED: {r.get('error', '?')[:60]}")
            results["failed"].append(acronym)

    print()
    print("=" * 60)
    print(f"Downloaded: {len(results['ok'])}")
    print(f"Already existed: {len(results['exists'])}")
    print(f"Failed: {len(results['failed'])}: {results['failed']}")
    print(f"Skipped: {len(results['skipped'])}")
    total = len(results["ok"]) + len(results["exists"])
    print(f"Total BioPortal available: {total}")


if __name__ == "__main__":
    main()
