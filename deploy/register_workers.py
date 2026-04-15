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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--central", default="http://localhost:8000")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text())
    total = sum(len(w["ontologies"]) for w in plan["workers"])
    print(f"Registering {total} ontologies across {len(plan['workers'])} workers\n")

    ok, fail = 0, 0
    failures = []
    for w in plan["workers"]:
        n = w["number"]
        worker_url = f"http://aberowl-worker-{n}:8080"
        for ont_id in w["ontologies"]:
            if args.dry_run:
                print(f"DRY  worker-{n:>2} {ont_id:30s} -> {worker_url}")
                continue
            success, msg = register(args.central, ont_id, worker_url)
            if success:
                ok += 1
            else:
                fail += 1
                failures.append((ont_id, n, msg))
                print(f"FAIL worker-{n:>2} {ont_id:30s} {msg}")

    print()
    print(f"Registered: {ok}  Failed: {fail}")
    if failures:
        print(f"\nFirst 10 failures:")
        for ont, n, msg in failures[:10]:
            print(f"  {ont} (worker-{n}): {msg}")


if __name__ == "__main__":
    main()
