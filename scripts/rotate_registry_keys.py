#!/usr/bin/env python3
"""Rotate the per-ontology registry ``secret_key`` for every entry in the
AberOWL central registry (Redis hash ``registered_servers``).

Why: the ``/api/getOntology`` leak fixed in #65 exposed these per-ontology keys.
Deploying #65 stops further leakage; running this invalidates every key that was
scraped through the old endpoint. Only the ``secret_key`` field is replaced (with
a fresh uuid4); all other fields are preserved untouched.

Redis is the source of truth for the registry. ``app/servers.json`` is only a
COLD-START seed (``_load_servers_from_file`` loads it *only* when the Redis hash
is absent). So this also rewrites ``servers.json`` to match — otherwise a Redis
wipe + restart would resurrect the old, leaked keys.

Safety (why rotation is non-breaking): the registry ``secret_key`` is read in
exactly three places in ``central_server/app/main.py`` / ``intake/updater.py``:

  1. ``/register`` re-registration auth  — legitimate only for self-registering
     workers; the deployed workers do NOT self-register (``ABEROWL_REGISTER``
     unset -> defaults false).
  2. ``/webhook/<id>`` update trigger    — ``X-Webhook-Secret``; no external
     webhook is configured with these keys.
  3. updater worker-auth fallback        — ``os.getenv("ABEROWL_SECRET_KEY") or
     registry_entry["secret_key"]``; the env key wins whenever central's
     ``ABEROWL_SECRET_KEY`` is set (it is on the deployment), so this fallback is
     never reached.

There are no worker restarts, no ontology reloads, and no downtime.

This script talks to Redis and to ``servers.json``, both of which live inside the
central-server container, so run it THERE:

    docker cp scripts/rotate_registry_keys.py deploy-central-server-1:/tmp/
    docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py            # dry-run
    docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py --apply    # rotate

Back up Redis first (the registry lives in the ``deploy_redis_data`` volume); the
rotation is reversible only from that snapshot. See ``deploy/README.md``.

Overrides: ``REDIS_URL`` env (default ``redis://redis``), ``--servers-file``
(default ``/code/app/servers.json``), ``--no-servers-file`` to skip the seed rewrite.
"""
import argparse
import json
import os
import sys
import uuid
from collections import Counter

REGISTRY_KEY = "registered_servers"
DEFAULT_SERVERS_FILE = "/code/app/servers.json"


def _mask(k):
    return "<none>" if not k else f"{k[:4]}..{k[-2:]}(len{len(k)})"


def _key_len_distribution(values):
    dist = Counter()
    for raw in values:
        k = json.loads(raw).get("secret_key")
        dist["none" if k is None else str(len(k))] += 1
    return dict(dist)


def main():
    ap = argparse.ArgumentParser(description="Rotate registry secret_keys (dry-run by default).")
    ap.add_argument("--apply", action="store_true",
                    help="rotate keys and rewrite servers.json (default: dry-run, mutate nothing)")
    ap.add_argument("--servers-file", default=DEFAULT_SERVERS_FILE,
                    help=f"cold-start seed to rewrite after rotation (default: {DEFAULT_SERVERS_FILE})")
    ap.add_argument("--no-servers-file", action="store_true",
                    help="skip rewriting the servers.json seed")
    args = ap.parse_args()

    import redis  # deferred so --help works without the dep (script runs in-container)

    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis"), decode_responses=True)
    entries = r.hgetall(REGISTRY_KEY)
    total = len(entries)
    print(f"registry entries: {total}")
    print(f"secret_key length distribution (before): {_key_len_distribution(entries.values())}")

    keyless = [f for f, raw in entries.items() if not json.loads(raw).get("secret_key")]
    if keyless:
        # A keyless entry is claimable by anyone via /register (main.py issues a
        # new key when none is stored). Rotation only REPLACES existing keys; it
        # deliberately does not mint keys for keyless entries (that would change
        # auth behavior beyond rotation). Report them for separate handling.
        print(f"NOTE: {len(keyless)} entries have no secret_key and will be left untouched: "
              f"{keyless[:10]}{'...' if len(keyless) > 10 else ''}")

    to_rotate = [f for f in entries if f not in set(keyless)]

    if not args.apply:
        sample_field = to_rotate[0] if to_rotate else (next(iter(entries), None))
        if sample_field:
            d = json.loads(entries[sample_field])
            others = sorted(k for k in d if k != "secret_key")
            print(f"sample entry '{d.get('ontology') or d.get('ontology_id') or sample_field}': "
                  f"secret_key {_mask(d.get('secret_key'))}; "
                  f"{len(others)} other fields preserved, e.g. {others[:6]}")
        print(f"DRY-RUN: would rotate {len(to_rotate)} entries. Re-run with --apply to mutate.")
        return

    pipe = r.pipeline()
    for field in to_rotate:
        d = json.loads(entries[field])
        d["secret_key"] = str(uuid.uuid4())
        pipe.hset(REGISTRY_KEY, field, json.dumps(d))
    pipe.execute()
    print(f"rotated secret_key on {len(to_rotate)} entries")

    if not args.no_servers_file:
        try:
            servers = [json.loads(v) for v in r.hvals(REGISTRY_KEY)]
            with open(args.servers_file, "w") as f:
                json.dump(servers, f, indent=4)
            print(f"rewrote {args.servers_file} with {len(servers)} entries")
        except OSError as e:
            print(f"WARNING: could not rewrite {args.servers_file}: {e}", file=sys.stderr)

    print(f"secret_key length distribution (after): {_key_len_distribution(r.hvals(REGISTRY_KEY))}")
    print("DONE.")


if __name__ == "__main__":
    main()
