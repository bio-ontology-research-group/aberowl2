#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Targeted re-download of specific ontologies that failed the bulk run.

Unlike download_ontologies.py (fixed beta set) and download_bioportal.py
(whole catalog), this takes an explicit list of ontology ids and retries
just those. For each id it tries the OBO Foundry PURL first, then falls
back to the BioPortal direct-download API. Files land at the same path
the planner/workers expect: <dest>/<id>/<id>.owl.

Idempotent: an id whose file already exists above --min-size is skipped.
A freshly downloaded file below --min-size is treated as junk (BioPortal
often returns tiny HTML error pages) and deleted.

Usage (on onto):
    python3 deploy/retry_downloads.py /data/aberowl/ontologies \\
        --ids foodon maxo bto pw ...
    python3 deploy/retry_downloads.py /data/aberowl/ontologies \\
        --ids-file /tmp/missing.txt --results-json /tmp/retry.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OBO_URL = "http://purl.obolibrary.org/obo/{id}.owl"
BP_URL = "https://data.bioontology.org/ontologies/{ID}/download?apikey={key}"
BP_API_KEY = "24e0413e-54e0-11e0-9d7b-005056aa3316"


def _curl(url: str, dest: Path, max_time: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl", "-fSL", "--max-time", str(max_time), "-o", str(dest), url],
        capture_output=True, text=True, timeout=max_time + 30,
    )


def retry_one(ont_id: str, dest_dir: Path, min_size: int, max_time: int) -> dict:
    ont_dir = dest_dir / ont_id
    owl_path = ont_dir / f"{ont_id}.owl"

    if owl_path.exists() and owl_path.stat().st_size > min_size:
        return {"id": ont_id, "status": "exists", "size": owl_path.stat().st_size}

    ont_dir.mkdir(parents=True, exist_ok=True)

    # 1) OBO Foundry PURL
    try:
        r = _curl(OBO_URL.format(id=ont_id), owl_path, max_time)
        if r.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > min_size:
            return {"id": ont_id, "status": "ok", "source": "obo", "size": owl_path.stat().st_size}
    except Exception as e:
        r = None  # fall through to BioPortal

    # 2) BioPortal direct download (acronym is the uppercased id)
    owl_path.unlink(missing_ok=True)
    try:
        bp = _curl(BP_URL.format(ID=ont_id.upper(), key=BP_API_KEY), owl_path, max_time)
        if bp.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > min_size:
            return {"id": ont_id, "status": "ok", "source": "bioportal", "size": owl_path.stat().st_size}
        size = owl_path.stat().st_size if owl_path.exists() else 0
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "status": "failed", "size_seen": size,
                "error": (bp.stderr or "").strip()[:160] or "both OBO and BioPortal failed"}
    except Exception as e:
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "status": "error", "error": str(e)[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", type=Path, help="ontologies root, e.g. /data/aberowl/ontologies")
    ap.add_argument("--ids", nargs="+", default=[])
    ap.add_argument("--ids-file", type=Path)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-size", type=int, default=20000, help="bytes; smaller = junk (default 20KB)")
    ap.add_argument("--max-time", type=int, default=1800, help="curl timeout seconds (default 1800)")
    ap.add_argument("--results-json", type=Path)
    args = ap.parse_args()

    ids = list(args.ids)
    if args.ids_file:
        ids += [ln.strip() for ln in args.ids_file.read_text().splitlines() if ln.strip()]
    ids = sorted(set(ids))
    if not ids:
        ap.error("provide --ids or --ids-file")

    print(f"Retrying {len(ids)} ontologies into {args.dest} (min-size {args.min_size}B)\n")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(retry_one, i, args.dest, args.min_size, args.max_time): i for i in ids}
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            if r["status"] in ("ok", "exists"):
                mb = r.get("size", 0) / 1024 / 1024
                src = r.get("source", "disk")
                print(f"  [{r['status']:6s}] {r['id']:16s} {mb:7.1f} MB  ({src})")
            else:
                print(f"  [FAIL  ] {r['id']:16s} {r.get('error','?')[:70]}")

    ok = [r for r in results if r["status"] == "ok"]
    exists = [r for r in results if r["status"] == "exists"]
    fail = [r for r in results if r["status"] not in ("ok", "exists")]
    print(f"\nRecovered: {len(ok)}  Already present: {len(exists)}  Still failing: {len(fail)}")
    if fail:
        print("Still failing:", sorted(r["id"] for r in fail))
    if args.results_json:
        args.results_json.write_text(json.dumps(results, indent=2))
        print(f"\nWrote {args.results_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
