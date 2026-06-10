#!/usr/bin/env python3
"""
Re-point an ontology's central-registry entry to a different worker.

WHY THIS EXISTS
---------------
The central registry is a Redis hash `registered_servers` on the central
server: ontologyId -> {ontology, url, status, secret_key}. The dispatcher
forwards each query to the worker URL stored here. When we move an ontology
to a different worker, its data and this registry entry must change in
lockstep, or queries route to the wrong (old) worker and fail.

`/register` would do this, but every existing entry is protected by a
secret_key that was issued at bulk-registration time and never saved
(deploy/register_workers.py discards it). So re-POSTing /register without
the key gets a 403. This tool edits the registry directly instead --
we own the database -- changing ONLY the `url` field and preserving the
existing secret_key and status.

It talks to Redis via `docker exec <container> redis-cli`, so run it on the
host where the central-redis container lives (onto). Reads use HGET; the
single write per ontology uses `redis-cli -x HSET` with the JSON piped on
stdin to avoid shell-quoting issues.

SAFETY
------
- Dry-run by default. Nothing is written unless you pass --apply.
- Only `url` (and optionally `status`) is changed. secret_key is preserved.
- Refuses to touch an ontology that has no existing entry (use
  deploy/register_workers.py for brand-new ids).
- Prints a before -> after diff for every change.

USAGE (on onto)
---------------
    # Read-only: inspect current entries
    python3 scripts/repoint_ontology.py --sudo --inspect mesh icd10pcs

    # Dry-run a move of mesh onto worker-16
    python3 scripts/repoint_ontology.py --sudo --worker-n 16 --ontology mesh

    # Actually apply it
    python3 scripts/repoint_ontology.py --sudo --worker-n 16 --ontology mesh --apply

    # Move several ids onto the same worker, mark them online
    python3 scripts/repoint_ontology.py --sudo --worker-n 30 \
        --ontology a --ontology b --ontology c --set-online --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

REGISTRY_KEY = "registered_servers"


def docker_base(args) -> list[str]:
    base = ["sudo"] if args.sudo else []
    base += ["docker", "exec"]
    return base


def redis_get(args, field: str) -> str | None:
    """HGET registered_servers <field>. Returns the raw value or None if absent."""
    cmd = docker_base(args) + [args.redis_container, "redis-cli", "HGET", REGISTRY_KEY, field]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"redis HGET failed for {field!r}: {out.stderr.strip() or out.stdout.strip()}")
    val = out.stdout.rstrip("\n")
    return val if val else None


def redis_set(args, field: str, value: str) -> None:
    """HSET registered_servers <field> <value>, value piped on stdin via -x."""
    cmd = docker_base(args) + ["-i", args.redis_container, "redis-cli", "-x", "HSET", REGISTRY_KEY, field]
    out = subprocess.run(cmd, input=value, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"redis HSET failed for {field!r}: {out.stderr.strip() or out.stdout.strip()}")


def worker_url(args) -> str:
    # Dispatch rstrips the trailing slash (central main.py), so it is cosmetic,
    # but every existing registry entry stores one -- match the convention.
    if args.worker_url:
        url = args.worker_url
    else:
        url = f"http://aberowl-worker-{args.worker_n}:8080"
    return url if url.endswith("/") else url + "/"


def inspect(args) -> int:
    for ont in args.inspect:
        raw = redis_get(args, ont)
        if raw is None:
            print(f"  {ont:30s} (no entry)")
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  {ont:30s} <unparseable> {raw[:120]}")
            continue
        key = entry.get("secret_key")
        key_note = "has-key" if key else "NO-KEY"
        print(f"  {ont:30s} {entry.get('status','?'):8s} {entry.get('url','?'):40s} [{key_note}]")
    return 0


def repoint(args) -> int:
    target = worker_url(args)
    print(f"Target worker URL: {target}")
    print(f"Mode: {'APPLY (writing)' if args.apply else 'DRY-RUN (no writes)'}\n")

    changed = skipped = missing = failed = 0
    for ont in args.ontology:
        raw = redis_get(args, ont)
        if raw is None:
            print(f"MISSING  {ont:30s} -- no registry entry; use deploy/register_workers.py for new ids")
            missing += 1
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            print(f"BADJSON  {ont:30s} -- entry is not valid JSON; skipping: {raw[:80]}")
            skipped += 1
            continue

        old_url = entry.get("url")
        old_status = entry.get("status")
        new_status = "online" if args.set_online else old_status

        if old_url == target and old_status == new_status:
            print(f"NOOP     {ont:30s} already at {target} (status {old_status})")
            skipped += 1
            continue

        # Mutate only url and (optionally) status; preserve secret_key + everything else.
        entry["url"] = target
        if args.set_online:
            entry["status"] = "online"

        status_note = f" status {old_status}->{entry.get('status')}" if old_status != entry.get("status") else ""
        print(f"REPOINT  {ont:30s} {old_url} -> {target}{status_note}")

        if args.apply:
            # The central server runs a periodic metadata-refresh that reads
            # every entry and writes it back; if it reads the old URL just
            # before our write and writes just after, it clobbers us. Re-read,
            # re-apply, and retry until the write sticks (verified by read-back).
            ok_write = False
            for attempt in range(5):
                redis_set(args, ont, json.dumps(entry))
                verify = json.loads(redis_get(args, ont) or "{}")
                if (verify.get("url") == target
                        and verify.get("secret_key") == entry.get("secret_key")):
                    ok_write = True
                    break
                time.sleep(2)
                cur = redis_get(args, ont)
                if cur:
                    try:
                        entry = json.loads(cur)
                        entry["url"] = target
                        if args.set_online:
                            entry["status"] = "online"
                    except json.JSONDecodeError:
                        pass
            if not ok_write:
                print(f"  VERIFY FAILED for {ont} after retries: url={verify.get('url')!r} (central refresh race)")
                failed += 1
                continue
        changed += 1

    print()
    verb = "Repointed" if args.apply else "Would repoint"
    print(f"{verb}: {changed}  Skipped/noop: {skipped}  Missing: {missing}  Failed: {failed}")
    if not args.apply and changed:
        print("\nRe-run with --apply to write these changes.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-point ontology registry entries to a worker (direct Redis edit).")
    ap.add_argument("--redis-container", default="aberowl-central-redis",
                    help="Name of the central Redis container (default: aberowl-central-redis)")
    ap.add_argument("--sudo", action="store_true", help="Prefix docker commands with sudo (needed on onto)")

    ap.add_argument("--inspect", nargs="+", metavar="ID",
                    help="Read-only: print current registry entries for these ontology ids and exit")

    ap.add_argument("--ontology", action="append", default=[], metavar="ID",
                    help="Ontology id to re-point (repeatable)")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--worker-n", type=int, help="Physical worker number; URL = http://aberowl-worker-N:8080")
    grp.add_argument("--worker-url", help="Explicit worker URL (overrides --worker-n)")

    ap.add_argument("--set-online", action="store_true",
                    help="Also set status=online (default: preserve existing status)")
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run)")

    args = ap.parse_args()

    if args.inspect:
        return inspect(args)

    if not args.ontology:
        ap.error("provide --inspect ID..., or --ontology ID with --worker-n/--worker-url")
    if args.worker_n is None and not args.worker_url:
        ap.error("--ontology requires a target: --worker-n N or --worker-url URL")
    return repoint(args)


if __name__ == "__main__":
    raise SystemExit(main())
