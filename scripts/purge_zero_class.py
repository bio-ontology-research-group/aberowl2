#!/usr/bin/env python3
"""Find (and optionally purge) registry entries that have no content AberOWL can
serve. AberOWL reasons over owl:Class hierarchies, so an entry with **0 owl:Class**
isn't browsable as a class hierarchy — but 0 classes does NOT mean empty: SKOS
concept schemes / DCAT catalogues have an ABox (individuals / skos:Concepts).

So this tool counts BOTH the TBox (owl:Class + OBO [Term]) and the ABox
(owl:NamedIndividual + skos:Concept) from the on-disk .owl (file-based, robust
vs a load-failure that merely reports 0), and splits 0-class entries into:

  - EMPTY      : 0 classes AND 0 ABox            -> safe to purge
  - ABOX-ONLY  : 0 classes but has individuals    -> NOT purged without --include-abox-only
                 (review: may be a SKOS/data ontology worth keeping or handling specially)

Dry-run by default. Runs on the host with the files + Redis container (onto).

    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1
    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1 --apply
    python3 scripts/purge_zero_class.py --sudo --apply --include-abox-only   # also purge SKOS/data
"""
import argparse, subprocess, sys


def sh(args, cmd):
    return subprocess.run((["sudo", "-n"] if args.sudo else []) + cmd,
                          capture_output=True, text=True).stdout


def redis(args, *a):
    return sh(args, ["docker", "exec", args.redis_container, "redis-cli"] + list(a))


def counts(args, path):
    """(tbox, abox) line counts, or (None, None) if the file is missing.
    tbox = owl:Class + OBO [Term]; abox = owl:NamedIndividual + skos:Concept."""
    out = sh(args, ["sh", "-c",
                    f"if [ -f '{path}' ]; then "
                    f"grep -cE 'owl:Class|\\[Term\\]' '{path}'; "
                    f"grep -cE 'owl:NamedIndividual|skos:Concept' '{path}'; "
                    f"else echo MISSING; fi"]).split()
    if out == ["MISSING"] or len(out) < 2:
        return None, None
    try:
        return int(out[0]), int(out[1])
    except ValueError:
        return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sudo", action="store_true")
    ap.add_argument("--redis-container", default="deploy-redis-1")
    ap.add_argument("--ontologies-dir", default="/data/aberowl/ontologies")
    ap.add_argument("--apply", action="store_true", help="HDEL flagged entries (default: dry-run)")
    ap.add_argument("--include-abox-only", action="store_true",
                    help="also purge 0-class entries that DO have an ABox (SKOS/data). "
                         "Off by default — those are surfaced for review, not auto-removed.")
    args = ap.parse_args()

    keys = [k.strip() for k in redis(args, "HKEYS", "registered_servers").splitlines() if k.strip()]
    print(f"Scanning {len(keys)} registered ontologies (TBox + ABox, file-based)...")
    empty, abox_only, missing = [], [], []
    for k in sorted(keys):
        tbox, abox = counts(args, f"{args.ontologies_dir}/{k}/{k}.owl")
        if tbox is None:
            missing.append(k)
        elif tbox == 0 and abox == 0:
            empty.append(k)
        elif tbox == 0:
            abox_only.append((k, abox))

    print(f"\nEMPTY (0 class, 0 ABox) — safe to purge: {len(empty)}")
    for k in empty:
        print(f"  {k}")
    print(f"\nABOX-ONLY (0 class, has individuals — REVIEW, not auto-purged): {len(abox_only)}")
    for k, n in abox_only:
        print(f"  {k}  (ABox: {n})")
    if missing:
        print(f"\nFILE MISSING — skipped (not purged): {len(missing)}: {missing}")

    to_purge = list(empty) + ([k for k, _ in abox_only] if args.include_abox_only else [])
    if not args.apply:
        print(f"\nDRY-RUN. --apply would purge {len(to_purge)} entries"
              f"{' (incl. ABox-only)' if args.include_abox_only else ' (EMPTY only; add --include-abox-only for SKOS/data)'}.")
        return 0
    for k in to_purge:
        redis(args, "HDEL", "registered_servers", k)
        print(f"  purged {k}")
    print(f"\nPurged {len(to_purge)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
