#!/usr/bin/env python3
"""Restart AberOWL workers one at a time, verifying each before moving on.

Written after a rollout on beta restarted 15 workers at once by accident, drove
the host to load average 119, and pushed an already-marginal JVM into
OutOfMemoryError. The two lessons are baked in here:

1. NEVER trust `docker logs | grep "Ontology loading sequence complete"` as a
   readiness signal. Docker retains output from *before* the restart, so the
   marker matches instantly and the script races ahead. This tool counts the
   marker before restarting and waits for a NEW one.
2. Readiness is confirmed against the worker API (`listLoadedOntologies`
   reporting `classified`), not against logs, and the count must match what the
   worker served beforehand.

Default behaviour is a dry run. Nothing restarts until you pass --apply.

Usage:
    # see what would happen, and the pre-flight state of each worker
    python3 deploy/rollout_worker.py --host 10.254.146.227 --workers 12

    # actually roll one worker, then stop
    python3 deploy/rollout_worker.py --host 10.254.146.227 --workers 12 --apply

    # roll several, sequentially, aborting if any fails to come back
    python3 deploy/rollout_worker.py --host 10.254.146.227 \
        --workers 1,3,5 --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

MARKER = "Ontology loading sequence complete"


def sh(host: str, cmd: str, timeout: int = 180) -> str:
    """Run a command on a remote host over ssh, returning stdout."""
    full = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, cmd]
    try:
        p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""


def docker(host: str, args: str, timeout: int = 180) -> str:
    # prod workers run with the invoking user in the docker group; beta needs sudo.
    return sh(host, f"docker {args} 2>/dev/null || sudo docker {args} 2>/dev/null", timeout)


def loaded(host: str, container: str, timeout: int = 120) -> tuple[int, int]:
    """Return (loaded, classified) as reported by the worker's own API."""
    raw = docker(
        host,
        f'exec {container} sh -c "curl -s --max-time 90 '
        f'http://localhost:8080/api/listLoadedOntologies.groovy"',
        timeout=timeout,
    )
    if not raw:
        return (-1, -1)
    try:
        data = json.loads(raw)
        onts = data.get("ontologies", [])
        return (len(onts), sum(1 for o in onts if o.get("status") == "classified"))
    except json.JSONDecodeError:
        return (-1, -1)


def rss_gb(host: str, container: str) -> float:
    out = docker(
        host,
        f"""exec {container} sh -c "ps -eo rss,args | grep [j]ava | head -1 | awk '{{print \\$1}}'" """,
    )
    try:
        return int(out) / 1048576
    except ValueError:
        return 0.0


def marker_count(host: str, container: str) -> int:
    out = docker(host, f"logs {container} 2>&1 | grep -c '{MARKER}'", timeout=240)
    try:
        return int(out.splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def roll(host: str, num: int, wait_s: int, apply: bool) -> bool:
    c = f"aberowl-worker-{num}"
    status = docker(host, f'ps --format "{{{{.Names}}}} {{{{.Status}}}}" | grep -w {c}')
    if not status:
        print(f"  {c}: NOT RUNNING — skipping (create it explicitly instead)")
        return False

    before_loaded, before_classified = loaded(host, c)
    before_rss = rss_gb(host, c)
    print(f"  {c}: {status}")
    if before_loaded < 0:
        print(f"    before: API NOT RESPONDING (worker is already broken), "
              f"RSS {before_rss:.1f} GB")
    else:
        print(f"    before: {before_classified} classified / {before_loaded} loaded, "
              f"RSS {before_rss:.1f} GB")

    if not apply:
        print("    DRY RUN — not restarting (pass --apply)")
        return True

    marks = marker_count(host, c)
    t0 = time.time()
    docker(host, f"restart {c}", timeout=300)
    print(f"    restarted; waiting for a NEW '{MARKER}' (had {marks})")

    deadline = t0 + wait_s
    while time.time() < deadline:
        if marker_count(host, c) > marks:
            break
        time.sleep(15)
    else:
        print(f"    TIMEOUT after {wait_s}s — worker did not report load completion")
        return False

    load_s = int(time.time() - t0)
    # Classification continues briefly after the marker; let it settle.
    time.sleep(30)
    after_loaded, after_classified = loaded(host, c)
    after_rss = rss_gb(host, c)

    print(f"    after : {after_classified} classified / {after_loaded} loaded, "
          f"RSS {after_rss:.1f} GB  (reload {load_s}s)")

    if after_loaded < 0:
        print("    !! FAILED: worker API not responding after restart. Stopping.")
        return False
    if after_classified == 0:
        print("    !! FAILED: worker came back with 0 classified ontologies. Stopping.")
        return False
    if after_classified < before_classified:
        print(f"    !! REGRESSION: {before_classified} -> {after_classified} classified. "
              f"Stopping. Investigate before continuing.")
        return False
    if before_rss > 0:
        print(f"    RSS {before_rss:.1f} -> {after_rss:.1f} GB "
              f"({100 * (1 - after_rss / before_rss):.0f}% lower)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="ssh target, e.g. a-zhapacfp@10.254.146.227")
    ap.add_argument("--workers", required=True, help="comma-separated worker numbers, in order")
    ap.add_argument("--wait", type=int, default=5400,
                    help="max seconds to wait for one worker to reload (default 5400; "
                         "NCBITaxon took over an hour on beta)")
    ap.add_argument("--apply", action="store_true", help="actually restart (default: dry run)")
    a = ap.parse_args()

    nums = [int(x) for x in a.workers.split(",") if x.strip()]
    print(f"host    : {a.host}")
    print(f"workers : {nums}  ({'APPLY' if a.apply else 'DRY RUN'})\n")

    for i, n in enumerate(nums, 1):
        print(f"[{i}/{len(nums)}] worker {n}")
        if not roll(a.host, n, a.wait, a.apply):
            print("\nAborting rollout — fix the above before continuing.")
            return 1
        print()

    print("All requested workers rolled successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
