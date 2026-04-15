#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Register each ontology in a worker plan with the central server.

Reads worker_plan.json, POSTs to central server's /register endpoint
for each ontology with the URL of its assigned worker container.

Usage (on onto):
    uv run deploy/register_workers.py \\
        --plan /data/aberowl/ontologies/worker_plan.json \\
        --central http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def register(central: str, ontology: str, worker_url: str) -> tuple[bool, str]:
    body = json.dumps({"ontology": ontology, "url": worker_url}).encode()
    req = Request(
        f"{central}/register", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            return True, resp.read().decode()[:200]
    except HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return False, str(e)[:200]


def fetch_registered_ids(central: str) -> set[str]:
    """Fetch currently registered ontology IDs from /api/servers."""
    try:
        with urlopen(f"{central}/api/servers", timeout=30) as resp:
            data = json.loads(resp.read())
        # Response format: list of {ontology, url, status, ...}
        return {s.get("ontology") for s in data if s.get("ontology")}
    except Exception as e:
        print(f"WARN: could not fetch /api/servers: {e}", file=sys.stderr)
        return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--central", default="http://localhost:8000")
    ap.add_argument("--rate-per-min", type=int, default=100,
                    help="Max registrations per minute (default 100; server caps at 120)")
    ap.add_argument("--retry-429", type=int, default=3,
                    help="Retries on HTTP 429 (with 60s backoff)")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip ontologies already in /api/servers")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text())
    total = sum(len(w["ontologies"]) for w in plan["workers"])
    print(f"Plan: {total} ontologies across {len(plan['workers'])} workers")

    existing: set[str] = set()
    if args.skip_existing:
        existing = fetch_registered_ids(args.central)
        print(f"Already registered: {len(existing)}")
    print()

    ok, fail, skipped = 0, 0, 0
    failures = []
    sleep_between = 60.0 / args.rate_per_min if args.rate_per_min > 0 else 0

    for w in plan["workers"]:
        n = w["number"]
        worker_url = f"http://aberowl-worker-{n}:8080"
        for ont_id in w["ontologies"]:
            if ont_id in existing:
                skipped += 1
                continue
            if args.dry_run:
                print(f"DRY  worker-{n:>2} {ont_id:30s} -> {worker_url}")
                continue
            for attempt in range(args.retry_429 + 1):
                success, msg = register(args.central, ont_id, worker_url)
                if success:
                    ok += 1
                    break
                if "429" in msg and attempt < args.retry_429:
                    print(f"     rate-limited on {ont_id}; sleeping 60s (attempt {attempt+1}/{args.retry_429})")
                    time.sleep(60)
                    continue
                fail += 1
                failures.append((ont_id, n, msg))
                print(f"FAIL worker-{n:>2} {ont_id:30s} {msg}")
                break
            if sleep_between:
                time.sleep(sleep_between)

    print()
    print(f"Registered: {ok}  Skipped (already registered): {skipped}  Failed: {fail}")
    if failures:
        print(f"\nFirst 10 failures:")
        for ont, n, msg in failures[:10]:
            print(f"  {ont} (worker-{n}): {msg}")


if __name__ == "__main__":
    main()
