#!/usr/bin/env python3
"""
Seed the central server registry with dummy ontology entries.

Registers N fake ontologies (offline workers) so the registry has enough
entries to make O(n) Redis scan overhead measurable in benchmarks.

Usage:
    python scripts/seed_registry.py --count 100          # add 100 dummy entries
    python scripts/seed_registry.py --clear              # remove all dummy_* entries
    python scripts/seed_registry.py --count 50 --url http://localhost:8000
"""

import argparse
import sys
import time
import requests


DUMMY_PREFIX = "dummy_bench_"


def seed(base_url: str, count: int) -> None:
    print(f"Seeding {count} dummy entries into {base_url} ...")
    ok = 0
    for i in range(count):
        ont_id = f"{DUMMY_PREFIX}{i:04d}"
        try:
            r = requests.post(
                f"{base_url}/register",
                json={
                    "ontology": ont_id,
                    "url": f"http://dummy-worker-{i:04d}.invalid:9999",
                },
                timeout=5,
            )
            if r.status_code == 200:
                ok += 1
            else:
                print(f"  WARN {ont_id}: HTTP {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  ERR {ont_id}: {e}", file=sys.stderr)
        # Small delay so the server's async metadata fetches don't pile up
        if i % 20 == 19:
            time.sleep(0.5)

    print(f"Done: {ok}/{count} registered.")
    print("Note: entries will appear 'offline' (expected — workers are fake).")


def clear(base_url: str) -> None:
    """Remove all dummy_bench_* entries via the admin Redis flush endpoint,
    falling back to individual deregistrations if that route doesn't exist."""
    print(f"Fetching server list from {base_url} ...")
    try:
        r = requests.get(f"{base_url}/api/servers", timeout=10)
        r.raise_for_status()
        servers = r.json()
    except Exception as e:
        print(f"Failed to fetch server list: {e}", file=sys.stderr)
        sys.exit(1)

    dummy = [s for s in servers if s.get("ontology", "").startswith(DUMMY_PREFIX)]
    if not dummy:
        print("No dummy entries found.")
        return

    print(f"Found {len(dummy)} dummy entries — removing via Redis CLI ...")
    import subprocess
    removed = 0
    for s in dummy:
        ont = s["ontology"]
        result = subprocess.run(
            ["docker", "exec", "aberowl-central-redis",
             "redis-cli", "HDEL", "registered_servers", ont],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            removed += 1
        else:
            print(f"  WARN could not remove {ont}: {result.stderr.strip()}", file=sys.stderr)

    print(f"Removed {removed}/{len(dummy)} dummy entries.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed/clear dummy registry entries.")
    parser.add_argument("--url", default="http://localhost:8000", help="Central server base URL")
    parser.add_argument("--count", type=int, default=100, help="Number of dummy entries to add")
    parser.add_argument("--clear", action="store_true", help="Remove all dummy entries instead of adding")
    args = parser.parse_args()

    if args.clear:
        clear(args.url)
    else:
        seed(args.url, args.count)


if __name__ == "__main__":
    main()
