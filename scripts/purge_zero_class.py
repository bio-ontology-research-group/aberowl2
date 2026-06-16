#!/usr/bin/env python3
"""Find (and optionally purge) registry entries that AberOWL can't serve as a
class hierarchy: **0 owl:Class per the reasoner**.

Detection uses the registry's reasoner-computed counts (`class_count`,
`individual_count`) — NOT text greps. Class declarations take many syntactic
forms (`owl:Class`, the full `owl#Class` URI, OWL/XML `<Class>`, default
namespaces) that greps miss, so a grep-based scan falsely flags real ontologies
(observed: acvd_ontology has 1719 classes but greps found 0). The reasoner count
in the registry (populated by the central's getStatistics refresh) is reliable.

0 classes != empty. SKOS schemes / DCAT catalogues have an ABox, so candidates
are split:
  - EMPTY     : class_count == 0 AND individual_count == 0  -> safe to purge
  - ABOX-ONLY : class_count == 0 AND individual_count  > 0  -> review only
                (NOT purged unless --include-abox-only)

Caveat: registry counts come from the last successful getStatistics refresh and
can be STALE for a just-(re)loaded ontology (e.g. one loaded since the last
refresh, or whose getStatistics is failing). Treat the dry-run as a candidate
list to VERIFY, not a blind purge list. Offline entries are reported separately
since their 0 may be a load failure, not a real 0-class.

    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1
    python3 scripts/purge_zero_class.py --sudo --redis-container deploy-redis-1 --apply
"""
import argparse, json, subprocess, sys


def sh(args, cmd):
    return subprocess.run((["sudo", "-n"] if args.sudo else []) + cmd,
                          capture_output=True, text=True).stdout


def redis(args, *a):
    return sh(args, ["docker", "exec", args.redis_container, "redis-cli"] + list(a))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sudo", action="store_true")
    ap.add_argument("--redis-container", default="deploy-redis-1")
    ap.add_argument("--apply", action="store_true", help="HDEL flagged entries (default: dry-run)")
    ap.add_argument("--include-abox-only", action="store_true",
                    help="also purge 0-class entries that have an ABox (SKOS/data). Off by default.")
    ap.add_argument("--include-offline", action="store_true",
                    help="also consider offline entries (their 0 may be a load failure — risky).")
    args = ap.parse_args()

    rows = []
    for l in redis(args, "HVALS", "registered_servers").splitlines():
        l = l.strip()
        if not l:
            continue
        try:
            rows.append(json.loads(l))
        except json.JSONDecodeError:
            pass

    empty, abox_only, offline_zero = [], [], []
    for d in rows:
        o = d.get("ontology")
        if (d.get("class_count") or 0) != 0:
            continue
        cls0_ind = d.get("individual_count") or 0
        if d.get("status") != "online":
            offline_zero.append((o, cls0_ind))
        elif cls0_ind == 0:
            empty.append(o)
        else:
            abox_only.append((o, cls0_ind))

    print(f"Scanned {len(rows)} registry entries (reasoner class_count).\n")
    print(f"EMPTY (online, 0 class, 0 individual) — safe to purge: {len(empty)}")
    for o in sorted(empty):
        print(f"  {o}")
    print(f"\nABOX-ONLY (online, 0 class, has individuals) — REVIEW, not auto-purged: {len(abox_only)}")
    for o, n in sorted(abox_only):
        print(f"  {o}  (individuals: {n})")
    print(f"\nOFFLINE + 0 class (could be a load failure / stale — NOT purged unless --include-offline): {len(offline_zero)}")
    for o, n in sorted(offline_zero):
        print(f"  {o}  (individuals: {n})")
    print("\nNOTE: registry counts can be stale for recently-(re)loaded ontologies — VERIFY before --apply.")

    to_purge = list(empty)
    if args.include_abox_only:
        to_purge += [o for o, _ in abox_only]
    if args.include_offline:
        to_purge += [o for o, _ in offline_zero]
    if not args.apply:
        print(f"\nDRY-RUN. --apply would purge {len(to_purge)} entries.")
        return 0
    for o in to_purge:
        redis(args, "HDEL", "registered_servers", o)
        print(f"  purged {o}")
    print(f"\nPurged {len(to_purge)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
