#!/usr/bin/env python3
"""
Download OWL files for the initial beta ontology set.

OBO Foundry: http://purl.obolibrary.org/obo/{id}.owl
BioPortal: requires API to get download URL

Usage:
    python3 deploy/download_ontologies.py /data/aberowl/ontologies
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OBO_URL_TEMPLATE = "http://purl.obolibrary.org/obo/{id}.owl"

# BioPortal ontologies with known direct download URLs
BIOPORTAL_URLS = {
    "SNOMEDCT": None,  # Too large / restricted, skip
    "MESH": "https://data.bioontology.org/ontologies/MESH/download?apikey=8b5b7825-538d-40e0-9e9e-5ab9274a9aeb&download_format=csv",
    "NCIT": "https://purl.obolibrary.org/obo/ncit.owl",
    "LOINC": None,  # Restricted
    "ICD10CM": None,  # Restricted
    "RXNORM": None,  # Restricted
    "RADLEX": "http://data.bioontology.org/ontologies/RADLEX/download?apikey=8b5b7825-538d-40e0-9e9e-5ab9274a9aeb",
    "FMA": "http://data.bioontology.org/ontologies/FMA/download?apikey=8b5b7825-538d-40e0-9e9e-5ab9274a9aeb",
    "EFO": "https://github.com/EBISPOT/efo/releases/latest/download/efo.owl",
    "EDAM": "https://edamontology.org/EDAM.owl",
    "SIO": "https://raw.githubusercontent.com/MaastrichtU-IDS/semanticscience/master/ontology/sio.owl",
    "BAO": "http://www.bioassayontology.org/bao/bao_complete.owl",
    "CLO": "https://purl.obolibrary.org/obo/clo.owl",
}


def download_obo(ont_id: str, dest_dir: Path) -> dict:
    """Download an OBO Foundry ontology."""
    ont_dir = dest_dir / ont_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    owl_path = ont_dir / f"{ont_id}.owl"

    if owl_path.exists() and owl_path.stat().st_size > 1000:
        return {"id": ont_id, "status": "exists", "size": owl_path.stat().st_size}

    url = OBO_URL_TEMPLATE.format(id=ont_id)
    try:
        result = subprocess.run(
            ["curl", "-fSL", "--max-time", "300", "-o", str(owl_path), url],
            capture_output=True, text=True, timeout=320,
        )
        if result.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > 100:
            return {"id": ont_id, "status": "ok", "size": owl_path.stat().st_size}
        else:
            owl_path.unlink(missing_ok=True)
            return {"id": ont_id, "status": "failed", "error": result.stderr[:200]}
    except Exception as e:
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "status": "error", "error": str(e)[:200]}


def download_bioportal(ont_id: str, url: str, dest_dir: Path) -> dict:
    """Download a BioPortal ontology from a direct URL."""
    ont_dir = dest_dir / ont_id.lower()
    ont_dir.mkdir(parents=True, exist_ok=True)
    owl_path = ont_dir / f"{ont_id.lower()}.owl"

    if owl_path.exists() and owl_path.stat().st_size > 1000:
        return {"id": ont_id, "status": "exists", "size": owl_path.stat().st_size}

    try:
        result = subprocess.run(
            ["curl", "-fSL", "--max-time", "600", "-o", str(owl_path), url],
            capture_output=True, text=True, timeout=620,
        )
        if result.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > 100:
            return {"id": ont_id, "status": "ok", "size": owl_path.stat().st_size}
        else:
            owl_path.unlink(missing_ok=True)
            return {"id": ont_id, "status": "failed", "error": result.stderr[:200]}
    except Exception as e:
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "status": "error", "error": str(e)[:200]}


def main():
    dest_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/data/aberowl/ontologies")
    config_path = Path(__file__).parent.parent / "central_server" / "config" / "beta_ontologies.json"

    with open(config_path) as f:
        config = json.load(f)

    obo_ids = config["obo_foundry"]
    bp_ids = config["bioportal"]

    print(f"Downloading {len(obo_ids)} OBO Foundry ontologies to {dest_dir}")
    print("=" * 60)

    results = {"ok": [], "failed": [], "skipped": [], "exists": []}

    # Download OBO ontologies in parallel (4 threads)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(download_obo, ont_id, dest_dir): ont_id for ont_id in obo_ids}
        for future in as_completed(futures):
            r = future.result()
            status = r["status"]
            if status in ("ok", "exists"):
                size_mb = r.get("size", 0) / 1024 / 1024
                print(f"  [{status:6s}] {r['id']:15s} ({size_mb:.1f} MB)")
                results[status].append(r["id"])
            else:
                print(f"  [FAIL  ] {r['id']:15s} - {r.get('error', '?')[:60]}")
                results["failed"].append(r["id"])

    print()
    print(f"Downloading BioPortal ontologies with known URLs")
    print("=" * 60)

    for bp_id in bp_ids:
        url = BIOPORTAL_URLS.get(bp_id)
        if not url:
            results["skipped"].append(bp_id)
            continue
        r = download_bioportal(bp_id, url, dest_dir)
        status = r["status"]
        if status in ("ok", "exists"):
            size_mb = r.get("size", 0) / 1024 / 1024
            print(f"  [{status:6s}] {r['id']:15s} ({size_mb:.1f} MB)")
            results[status].append(r["id"])
        else:
            print(f"  [FAIL  ] {r['id']:15s} - {r.get('error', '?')[:60]}")
            results["failed"].append(r["id"])

    print()
    print("=" * 60)
    print(f"Downloaded: {len(results['ok'])}")
    print(f"Already existed: {len(results['exists'])}")
    print(f"Failed: {len(results['failed'])}: {results['failed']}")
    print(f"Skipped (no URL): {len(results['skipped'])}")
    total = len(results["ok"]) + len(results["exists"])
    print(f"Total available: {total}")

    # Write a summary of available ontologies
    available = sorted(results["ok"] + results["exists"])
    summary_path = dest_dir / "available_ontologies.json"
    with open(summary_path, "w") as f:
        json.dump(available, f, indent=2)
    print(f"\nAvailable ontology list written to {summary_path}")


if __name__ == "__main__":
    main()
