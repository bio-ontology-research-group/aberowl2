# Production rollout: hppcrt iterator-pool fix

Applies the fix from #74 / #75 to production and recovers the offline ontologies.
Read this fully before starting. Every phase is independently valuable — you can
stop after any of them and leave production in a better state than you found it.

## Why this is worth doing

On beta the fix took the full 859-ontology fleet from **630 GB of live heap to
94.8 GB** (RSS 805 -> 312 GB). Production has 16-core workers rather than beta's
256, so its pools are already 16 deep instead of 256 and the win there is
smaller — expect roughly 20-30% off live heap, not 85%. The bigger prize on prod
is that it unblocks hosting the *whole* fleet: ~95 GB of live heap fits on a
single 157 GB host.

## Hosts

| role | address | cores | RAM | notes |
|---|---|---:|---:|---|
| main | 10.254.147.137 | 8 | 15 GB | central + ES + redis + nginx |
| worker1 | 10.254.146.227 | 16 | 157 GB | workers 1,3,5,7,9,12,16 |
| worker2 | 10.254.146.61 | 16 | 157 GB | workers 2,4,6,8,10,11,13,15,17 |

Log in as `a-zhapacfp`. That account is in the `docker` group, so **`docker` works
without sudo** (there is no passwordless sudo — don't reach for it).

Code lives at `/opt/aberowl2` and is mounted read-only into the containers, so a
code change takes effect on worker restart. Ontologies are at
`/opt/aberowl2/ontologies` (462 ontologies, 6.2 GB; 62 GB free).

## Before you start: back up

`CLAUDE.md` requires a backup before any deploy. At minimum, on each worker host:

```bash
ts=$(date +%Y%m%d_%H%M%S)
mkdir -p ~/aberowl_backup_$ts
cp /opt/aberowl2/aberowlapi/server_manager.py ~/aberowl_backup_$ts/
cp /opt/aberowl2/ontologies/worker_*_config.json ~/aberowl_backup_$ts/
docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' > ~/aberowl_backup_$ts/containers.txt
for c in $(docker ps --format '{{.Names}}' | grep worker); do
  docker inspect $c > ~/aberowl_backup_$ts/inspect_$c.json
done
```

Also record the live registry so you can prove what changed:

```bash
curl -s http://aber-owl.net/api/servers > ~/aberowl_backup_$ts/registry_before.json
```

## Phase 1 — the canary (`worker-12`)

`aberowl-worker-12` on **worker1** died with `java.lang.OutOfMemoryError: Java heap
space` on 2026-07-19 at `-Xmx8g`. Its API does not respond and it serves none of
its 14 ontologies. It is the ideal first target: there is no working state to
lose, and ~90% of what it was failing to allocate was iterator pools.

1. Copy the merged `server_manager.py` (from `main`, commit `04f416c` or later) to
   **both** worker hosts:

   ```bash
   scp aberowlapi/server_manager.py a-zhapacfp@10.254.146.227:/opt/aberowl2/aberowlapi/
   scp aberowlapi/server_manager.py a-zhapacfp@10.254.146.61:/opt/aberowl2/aberowlapi/
   ```

   Copying is inert until a worker restarts. Do **not** use `deploy/deploy.sh --sync`:
   its `rsync --delete` excludes only `data/` and `ontologies/`, so it would delete
   `backups/`, `env_files/` and `logs/`.

2. Dry run, then apply:

   ```bash
   python3 deploy/rollout_worker.py --host a-zhapacfp@10.254.146.227 --workers 12
   python3 deploy/rollout_worker.py --host a-zhapacfp@10.254.146.227 --workers 12 --apply
   ```

3. Success looks like worker-12 reporting **14 classified** where it previously
   reported nothing. If it still OOMs, the ontologies genuinely need more than
   8 GB even without the pools — recreate it with a larger `-Xmx` (see phase 4).

## Phase 2 — roll the remaining workers

**One at a time. Never in parallel.** On beta, restarting 15 workers at once drove
the host to load average 119 and pushed a marginal JVM into OOM. Production has
16 cores against beta's 256; it will cope far worse.

`rollout_worker.py` enforces this: it counts the log completion marker *before*
restarting and waits for a new one (a plain `grep` matches pre-restart output and
races ahead), then verifies against the worker API and aborts the whole run if a
worker comes back with fewer classified ontologies than it had.

Go smallest-first so the quick wins land early:

```bash
# worker1 — 16 is the big one (300 ontologies), leave it last
python3 deploy/rollout_worker.py --host a-zhapacfp@10.254.146.227 --workers 9,1,3,5,7,16 --apply

# worker2
python3 deploy/rollout_worker.py --host a-zhapacfp@10.254.146.61 --workers 2,4,6,8,10,11,13,15,17 --apply
```

Expect wide variation in reload time. Measured on beta: 120 small ontologies came
back in **15 s**, a 40-ontology worker in **292 s**, larger ones in **713 s** and
**794 s**, and the NCBITaxon worker took **over an hour**. Each worker is offline
for its whole reload and production has no redundancy — every ontology lives on
exactly one worker.

## Phase 3 — recover `worker-14`

The other 80 offline ontologies belong to `worker_14`, which has no container at
all, though `/opt/aberowl2/ontologies/worker_14_config.json` still exists. Recreate
it from that config using the same `docker run` shape as its siblings — copy the
flags from a working worker's `docker inspect` output (captured in your backup).

Phases 1 and 3 together close all 94 offline ontologies for roughly zero extra RAM.

## Phase 4 — right-size `-Xmx` (optional, second pass)

`docker restart` preserves the environment, so phases 1-3 apply the code fix but
**not** new heap settings. Changing `-Xmx` needs `docker rm` + `docker run`.

Do this only after measuring, and size from the post-fix live heap. On beta the
workers still carry their old oversized values (one holds `-Xmx320g` for 24.9 GB
of live heap), which is why RSS there remains ~3x live. A reasonable rule is
`-Xmx` at roughly 2x measured live heap. Measure with:

```bash
docker exec <container> jcmd 7 GC.run
docker exec <container> jcmd 7 GC.heap_info   # read post-GC "used"
```

## Phase 5 — add the 397 missing ontologies

Production has 462 of beta's 859. The missing 397 are **7.9 GB** of OWL files;
prod has 62 GB free. Four files are half the bulk (ncbitaxon 1.85 GB, mesh 1.0 GB,
bero 878 MB, loinc 705 MB) and 274 of the 397 are under 1 MB.

Copy them from beta (`onto:/data/aberowl/ontologies/<id>/<id>.owl`) rather than
re-downloading from source — beta's copies already went through
`fix_ontology_files.py`, whereas a fresh intake reintroduces the parse and 404
failures.

The exact list is committed as `deploy/missing_from_prod_2026-08-02.txt` (397
paths, largest first, relative to the ontologies directory on both ends).

**Routing.** Verified 2026-08-02: beta and prod can reach each other's port 22 in
both directions, but *neither has credentials for the other* (prod's
`~/.ssh` has no keys). So don't try to make one pull from the other directly.
Use agent forwarding from the laptop, which already authenticates to both:

```bash
eval $(ssh-agent) && ssh-add          # laptop has no agent running by default
ssh -A onto
rsync -av --files-from=/tmp/missing_from_prod_2026-08-02.txt \
      /data/aberowl/ontologies/ \
      a-zhapacfp@10.254.146.227:/opt/aberowl2/ontologies/
```

Copy the list to `onto:/tmp/` first. Note the trailing slashes, and that plain
`rsync` without `--delete` is safe here — it only adds directories. Split the list
across the two worker hosts according to where you intend the new workers to run.

Then extend the worker configs (or add workers), register with the central server,
and reindex.

## Rollback

The fix is one file and one env var.

- **Per worker, immediately:** set `HPPC_ITERATOR_POOLSIZE` to the old behaviour
  by recreating the container with `-e HPPC_ITERATOR_POOLSIZE=<core count>`, or
  restore `server_manager.py` from your backup and restart.
- The change only takes effect on restart, so an untouched worker is already at
  the old behaviour.

## Known-good reference numbers (beta, post-fix)

| worker | ontologies | live heap | RSS |
|---|---:|---:|---:|
| worker_21 (incl. NCBITaxon) | 7 | 24.9 GB | 87.8 GB |
| worker_22 | 22 | 24.1 GB | 61.5 GB |
| worker_23 | 39 | 18.8 GB | 55.8 GB |
| worker_30 | 120 | 0.90 GB | 3.5 GB |
| **fleet total** | **847 classified** | **94.8 GB** | **311.9 GB** |
