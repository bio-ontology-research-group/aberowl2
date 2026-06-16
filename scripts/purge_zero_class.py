#!/usr/bin/env python3
"""Find (and optionally purge) registry entries that are not real OWL class
ontologies — i.e. 0 owl:Class. These are typically SKOS concept schemes or DCAT
catalogues (e.g. ncod, shedding-hub) that ELK has nothing to classify and the UI
can't browse as a hierarchy.

Detection is **file-based** (counts class declarations in the on-disk .owl),
not the registry's cached class_count — so it distinguishes a genuine
non-ontology from one that merely failed to load and happens to report 0.
Counts both `owl:Class` (RDF/XML, Turtle) and `[Term]` (OBO format); SKOS
`skos:Concept` is intentionally NOT counted.

Runs on the server that has the ontology files + the Redis container (onto).
Dry-run by default; --apply HDELs the flagged entries from registered_servers.

    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1
    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1 --apply
"""
import argparse, subprocess, sys


def sh(args, cmd):
    pre = (["sudo", "-n"] if args.sudo else [])
    return subprocess.run(pre + cmd, capture_output=True, text=True).stdout


def redis(args, *redis_args):
    return sh(args, ["docker", "exec", args.redis_container, "redis-cli"] + list(redis_args))


def class_count(args, path):
    """Lines declaring a class (owl:Class or OBO [Term]); -1 if file missing."""
    out = sh(args, ["sh", "-c",
                    f"if [ -f '{path}' ]; then grep -cE 'owl:Class|\\[Term\\]' '{path}'; "
                    f"else echo -1; fi"]).strip()
    try:
        return int(out)
    except ValueError:
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sudo", action="store_true", help="prefix docker/grep with sudo -n")
    ap.add_argument("--redis-container", default="deploy-redis-1")
    ap.add_argument("--ontologies-dir", default="/data/aberowl/ontologies")
    ap.add_argument("--apply", action="store_true",
                    help="HDEL the flagged 0-class entries (default: dry-run, list only)")
    args = ap.parse_args()

    keys = [k.strip() for k in redis(args, "HKEYS", "registered_servers").splitlines() if k.strip()]
    print(f"Scanning {len(keys)} registered ontologies (file-based owl:Class count)...")
    zero, missing = [], []
    for k in sorted(keys):
        path = f"{args.ontologies_dir}/{k}/{k}.owl"
        c = class_count(args, path)
        if c < 0:
            missing.append(k)
        elif c == 0:
            zero.append(k)

    print(f"\n0-class (non-ontology) candidates: {len(zero)}")
    for k in zero:
        print(f"  {k}")
    if missing:
        print(f"\nFile missing — skipped (NOT purged), may need re-download: {len(missing)}")
        for k in missing:
            print(f"  {k}")

    if not args.apply:
        print(f"\nDRY-RUN. Re-run with --apply to HDEL the {len(zero)} flagged entries.")
        return 0

    for k in zero:
        redis(args, "HDEL", "registered_servers", k)
        print(f"  purged {k}")
    print(f"\nPurged {len(zero)} entries from registered_servers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
