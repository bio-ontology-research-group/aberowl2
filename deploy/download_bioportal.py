#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Download all OWL files from BioPortal using the REST API.

Fetches the full ontology catalog dynamically (filter out summaryOnly
entries which have no downloadable content), then downloads each in
parallel.

OBO Foundry ontologies are NEVER overwritten: anything whose lowercased
acronym is in the OBO Foundry registry is skipped entirely, so the OBO
versions (fetched by download_ontologies.py) remain authoritative. As a
second layer of protection, any file already on disk larger than the
--min-size threshold is also skipped. Freshly downloaded files smaller
than --min-size are treated as failures and deleted — this rejects the
many BP entries that return tiny HTML error pages or metadata-only
stubs (~380 such files were found on a full catalog pull at the 100KB
threshold; ~20KB is a reasonable default to catch junk without trimming
legitimate small ontologies).

Usage:
    uv run deploy/download_bioportal.py /data/aberowl/ontologies
    uv run deploy/download_bioportal.py /data/aberowl/ontologies --workers 8 --log /tmp/bp.log --min-size 50000
"""

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request

API_KEY = "24e0413e-54e0-11e0-9d7b-005056aa3316"
API_BASE = "https://data.bioontology.org"
OBO_REGISTRY = "http://purl.obolibrary.org/meta/ontologies.jsonld"


def fetch_catalog() -> list[dict]:
    req = Request(
        f"{API_BASE}/ontologies?apikey={API_KEY}",
        headers={"Accept": "application/json"},
    )
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return [o for o in data if not o.get("summaryOnly")]


def fetch_obo_foundry_ids() -> set[str]:
    """Return lowercased IDs of all registered OBO Foundry ontologies."""
    with urlopen(OBO_REGISTRY, timeout=60) as resp:
        data = json.loads(resp.read())
    return {o["id"].lower() for o in data.get("ontologies", []) if o.get("id")}


def download_one(acronym: str, dest_dir: Path, min_size: int) -> dict:
    ont_id = acronym.lower()
    ont_dir = dest_dir / ont_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    owl_path = ont_dir / f"{ont_id}.owl"

    if owl_path.exists() and owl_path.stat().st_size > min_size:
        return {"id": ont_id, "acronym": acronym, "status": "exists", "size": owl_path.stat().st_size}

    url = f"{API_BASE}/ontologies/{acronym}/download?apikey={API_KEY}"
    try:
        # --compressed: request and transparently decompress Content-Encoding:
        # gzip responses (BioPortal returns several large ontologies this way;
        # without --compressed curl writes raw gzip bytes to disk and downstream
        # OWLAPI parsing fails with "Content is not allowed in prolog").
        result = subprocess.run(
            [
                "curl", "-fSL", "--compressed",
                "--max-time", "1800",
                "--retry", "2",
                "-H", f"Authorization: apikey token={API_KEY}",
                "-o", str(owl_path),
                url,
            ],
            capture_output=True, text=True, timeout=1850,
        )
        if result.returncode == 0 and owl_path.exists() and owl_path.stat().st_size > min_size:
            return {"id": ont_id, "acronym": acronym, "status": "ok", "size": owl_path.stat().st_size}
        else:
            # Remove tiny / empty files (HTML error pages etc.)
            if owl_path.exists() and owl_path.stat().st_size <= min_size:
                owl_path.unlink(missing_ok=True)
            err = (result.stderr or result.stdout or "")[:300].strip()
            return {"id": ont_id, "acronym": acronym, "status": "failed", "error": err}
    except subprocess.TimeoutExpired:
        owl_path.unlink(missing_ok=True)
        return {"id": ont_id, "acronym": acronym, "status": "timeout"}
    except Exception as e:
        return {"id": ont_id, "acronym": acronym, "status": "error", "error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default="/data/aberowl/ontologies", type=Path)
    ap.add_argument("--workers", type=int, default=6, help="parallel download workers")
    ap.add_argument("--log", type=Path, default=None, help="append-only progress log")
    ap.add_argument("--limit", type=int, default=0, help="limit number of acronyms (for testing)")
    ap.add_argument("--results-json", type=Path, default=None, help="write final results JSON")
    ap.add_argument(
        "--min-size", type=int, default=20_000,
        help="Minimum acceptable OWL file size in bytes. Files smaller than this are "
             "treated as failures and discarded. Default 20000 (20KB). Use a smaller "
             "value like 1000 to keep stub ontologies, or a larger one to aggressively "
             "filter out broken/metadata-only uploads.",
    )
    args = ap.parse_args()

    dest: Path = args.dest
    dest.mkdir(parents=True, exist_ok=True)

    log_fh = open(args.log, "a", buffering=1) if args.log else None

    def log(msg: str):
        print(msg, flush=True)
        if log_fh:
            log_fh.write(msg + "\n")

    log(f"[{time.strftime('%H:%M:%S')}] Fetching BioPortal catalog...")
    try:
        catalog = fetch_catalog()
    except Exception as e:
        log(f"FATAL: catalog fetch failed: {e}")
        sys.exit(1)

    log(f"[{time.strftime('%H:%M:%S')}] Fetching OBO Foundry registry (for skip-list)...")
    try:
        obo_ids = fetch_obo_foundry_ids()
    except Exception as e:
        log(f"FATAL: OBO Foundry registry fetch failed: {e}")
        sys.exit(1)
    log(f"[{time.strftime('%H:%M:%S')}] OBO Foundry has {len(obo_ids)} registered ontologies — these will be skipped")

    all_acronyms = sorted({o["acronym"] for o in catalog if o.get("acronym")})
    skipped_obo = [a for a in all_acronyms if a.lower() in obo_ids]
    acronyms = [a for a in all_acronyms if a.lower() not in obo_ids]
    log(f"[{time.strftime('%H:%M:%S')}] Catalog: {len(all_acronyms)}; skipping OBO overlap: {len(skipped_obo)}; BP-only to download: {len(acronyms)}")
    if args.limit:
        acronyms = acronyms[: args.limit]

    results: dict[str, list] = {"ok": [], "exists": [], "failed": [], "timeout": [], "error": []}
    total = len(acronyms)
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut_to_acr = {ex.submit(download_one, a, dest, args.min_size): a for a in acronyms}
        for fut in as_completed(fut_to_acr):
            r = fut.result()
            done += 1
            status = r["status"]
            results.setdefault(status, []).append(r["acronym"])
            size_mb = r.get("size", 0) / 1024 / 1024 if r.get("size") else 0
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            if status == "ok":
                log(f"[{done:4d}/{total}] OK       {r['acronym']:20s} {size_mb:7.1f} MB  (eta {eta/60:.1f}m)")
            elif status == "exists":
                # Quieter — one line, no size churn
                if done % 50 == 0 or done == total:
                    log(f"[{done:4d}/{total}] ({len(results['exists'])} existing skipped)")
            else:
                log(f"[{done:4d}/{total}] {status.upper():7s}  {r['acronym']:20s}  {r.get('error','')[:120]}")

    log("=" * 70)
    log(f"OBO Foundry overlap (skipped, use OBO download): {len(skipped_obo)}")
    log(f"Downloaded fresh: {len(results['ok'])}")
    log(f"Already existed: {len(results['exists'])}")
    log(f"Failed: {len(results['failed'])}")
    log(f"Timeouts: {len(results['timeout'])}")
    log(f"Errors: {len(results['error'])}")
    log(f"Total BP available on disk: {len(results['ok']) + len(results['exists'])} / {total}")

    if args.results_json:
        payload = dict(results)
        payload["obo_skipped"] = skipped_obo
        args.results_json.write_text(json.dumps(payload, indent=2))
        log(f"Results written to {args.results_json}")

    if log_fh:
        log_fh.close()


if __name__ == "__main__":
    main()
