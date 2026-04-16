#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Fetch per-ontology metadata from OBO Foundry or BioPortal and write
{ont_dir}/metadata.json for each ontology on disk.

Many BioPortal ontologies (and some OBO ones) don't embed a display
title/description as owl:Ontology annotations, so getStatistics.groovy
can't surface anything. This companion JSON is read as fallback by the
stats endpoint so the central registry and frontend show real names.

Source resolution per ontology:
  1. If the lowercased id is in the OBO Foundry registry -> obofoundry
  2. Else try BioPortal with acronym = id.upper() -> bioportal
  3. Else write a stub with just the id

Usage (backfill all on-disk ontologies):
    uv run deploy/fetch_metadata.py /data/aberowl/ontologies
    uv run deploy/fetch_metadata.py /data/aberowl/ontologies --force --workers 8
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BP_API_KEY = "24e0413e-54e0-11e0-9d7b-005056aa3316"
BP_API = "https://data.bioontology.org"
OBO_REGISTRY_URL = "http://purl.obolibrary.org/meta/ontologies.jsonld"


def load_obo_registry() -> dict[str, dict]:
    with urlopen(OBO_REGISTRY_URL, timeout=60) as resp:
        data = json.loads(resp.read())
    return {o["id"].lower(): o for o in data.get("ontologies", []) if o.get("id")}


def normalize_license(lic) -> str:
    if isinstance(lic, dict):
        return lic.get("url") or lic.get("label") or ""
    if isinstance(lic, str):
        return lic
    return ""


def obo_to_metadata(ont_id: str, entry: dict) -> dict:
    contact = entry.get("contact") or {}
    creator = contact.get("label") if isinstance(contact, dict) else None
    return {
        "source": "obofoundry",
        "id": ont_id,
        "acronym": ont_id.upper(),
        "title": entry.get("title", "") or "",
        "description": entry.get("description", "") or "",
        "home_page": entry.get("homepage", "") or "",
        "documentation": entry.get("documentation", "") or "",
        "license": normalize_license(entry.get("license")),
        "publication": entry.get("publications", [{}])[0].get("id", "") if entry.get("publications") else "",
        "version_iri": "",
        "creators": [creator] if creator else [],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def bp_fetch(acronym: str, retries: int = 2) -> dict | None:
    url = f"{BP_API}/ontologies/{acronym}?apikey={BP_API_KEY}"
    req = Request(url, headers={"Accept": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (404, 403, 422):
                return None
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def bp_fetch_latest_submission(acronym: str) -> dict | None:
    """Submission has richer fields — description, documentation, license."""
    url = f"{BP_API}/ontologies/{acronym}/latest_submission?apikey={BP_API_KEY}&display=description,documentation,homepage,license,publication,released,version,contact"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def bp_to_metadata(ont_id: str, cat_entry: dict, sub: dict | None) -> dict:
    sub = sub or {}
    contacts = sub.get("contact") or []
    creators = [c.get("name", "") for c in contacts if isinstance(c, dict)]
    lic = sub.get("hasLicense") or sub.get("license") or ""
    return {
        "source": "bioportal",
        "id": ont_id,
        "acronym": cat_entry.get("acronym", ont_id.upper()),
        "title": cat_entry.get("name", "") or sub.get("name", "") or "",
        "description": sub.get("description", "") or "",
        "home_page": sub.get("homepage", "") or "",
        "documentation": sub.get("documentation", "") or "",
        "license": lic if isinstance(lic, str) else "",
        "publication": (sub.get("publication") or [""])[0] if isinstance(sub.get("publication"), list) else (sub.get("publication") or ""),
        "version_iri": sub.get("version", "") or "",
        "creators": [c for c in creators if c],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def stub_metadata(ont_id: str) -> dict:
    return {
        "source": "unknown",
        "id": ont_id,
        "acronym": ont_id.upper(),
        "title": "",
        "description": "",
        "home_page": "",
        "documentation": "",
        "license": "",
        "publication": "",
        "version_iri": "",
        "creators": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_one(ont_id: str, dest: Path, obo_registry: dict[str, dict],
              force: bool) -> tuple[str, str]:
    meta_path = dest / ont_id / "metadata.json"
    if meta_path.exists() and not force:
        return (ont_id, "exists")

    if not meta_path.parent.is_dir():
        return (ont_id, "no_dir")

    if ont_id.lower() in obo_registry:
        meta = obo_to_metadata(ont_id, obo_registry[ont_id.lower()])
        status = "obo"
    else:
        acronym = ont_id.upper()
        cat = bp_fetch(acronym)
        if cat is None:
            meta = stub_metadata(ont_id)
            status = "stub"
        else:
            sub = bp_fetch_latest_submission(acronym)
            meta = bp_to_metadata(ont_id, cat, sub)
            status = "bp"

    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return (ont_id, status)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", type=Path)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing metadata.json")
    ap.add_argument("--only", type=str, default=None,
                    help="Only process a single ontology id (for testing)")
    args = ap.parse_args()

    print(f"Loading OBO Foundry registry...", flush=True)
    obo_reg = load_obo_registry()
    print(f"OBO Foundry has {len(obo_reg)} entries", flush=True)

    if args.only:
        ids = [args.only]
    else:
        ids = sorted(p.name for p in args.dest.iterdir()
                     if p.is_dir() and (p / f"{p.name}.owl").exists())
    print(f"Processing {len(ids)} ontologies with {args.workers} workers", flush=True)

    counts = {"obo": 0, "bp": 0, "stub": 0, "exists": 0, "no_dir": 0}
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, oid, args.dest, obo_reg, args.force): oid
                   for oid in ids}
        for fut in as_completed(futures):
            oid, status = fut.result()
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 50 == 0 or done == len(ids):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                print(f"[{done:4d}/{len(ids)}] rate={rate:.1f}/s counts={counts}", flush=True)

    print(f"\nDone in {time.time()-t0:.1f}s  {counts}")


if __name__ == "__main__":
    main()
