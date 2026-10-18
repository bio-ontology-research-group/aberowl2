# Deployment and administration

For one host and a chosen set of ontologies, use [Self-hosting](SELF_HOSTING.md).
The separate central and worker stacks support larger installations. Run the
commands here from the repository root on the deployment host. Keep host access,
proxy configuration and credentials in your private operations configuration.

## Central stack

Install Docker with Compose. Set `ADMIN_PASSWORD` and `ABEROWL_SECRET_KEY` in a
private environment file; workers must use the same shared secret. Set
`ONTOLOGIES_PATH` to the absolute ontology directory. `ADMIN_USER` defaults to
`admin`, `CENTRAL_PORT` to `8000`, and `MCP_ONTOLOGY_PORT` to `8766`.

```bash
docker network create aberowl-net
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env up -d --build
```

Create the network only if it does not already exist. This stack runs FastAPI,
Redis and Elasticsearch. The central image builds the frontend from source.
Named volumes retain the registry, indices and registry seed across rebuilds.
Keep the Compose project name stable to reuse those volumes.

The API is available at `http://localhost:8000`; MCP is at
`http://localhost:8766/mcp`. MCP binds to loopback by default. Set
`MCP_BIND_HOST` to an appropriate interface only when your proxy needs it, and
restrict direct access to the service ports. Configure TLS and public routes in
your own reverse proxy.

## Bulk ontology provisioning

The current bulk launcher requires a checkout at `/data/aberowl`, ontologies
under `/data/aberowl/ontologies`, the central Compose project `deploy`, Docker
network `aberowl-net`, and a worker image named `aberowl-api`. These are fixed
launcher conventions. For an arbitrary installation directory, use the self-host
stack instead.

Set `BIOPORTAL_API_KEY` in the environment before BioPortal downloads or metadata
requests. `download_ontologies.py` reads the retained catalogue in
`central_server/config/beta_ontologies.json`; `download_bioportal.py` discovers
BioPortal submissions. Access restrictions and licensing may prevent some downloads. Inspect
download outcomes before planning workers.

```bash
mkdir -p /data/aberowl/ontologies
uv run deploy/download_ontologies.py /data/aberowl/ontologies
uv run deploy/download_bioportal.py /data/aberowl/ontologies --min-size 20000
uv run deploy/fix_ontology_files.py /data/aberowl/ontologies --dry-run
uv run deploy/fix_ontology_files.py /data/aberowl/ontologies
find /data/aberowl/ontologies -maxdepth 2 -name '*.owl' -printf '%s %p\n' > /tmp/aberowl-sizes.txt
uv run deploy/plan_workers.py --sizes /tmp/aberowl-sizes.txt \
  --existing /data/aberowl/ontologies --out /data/aberowl/ontologies --start 1
docker build -f Dockerfile.api -t aberowl-api .
uv run deploy/launch_workers.py \
  --plan /data/aberowl/ontologies/worker_plan.json --env deploy/.env --port-start 8081
```

Choose unused worker numbers and host ports when extending an installation.
The planner skips ontologies assigned in existing worker configurations. Review
its memory allocations before launch: OWL parsing, import merging and
classification can require much more memory than the file size. The launcher
reserves JVM overhead outside the heap. If a worker exhausts memory, revise its
`ram_gb` allocation and use the launcher's `--recreate` option for that worker.

After workers finish classification, register their ontologies and fetch display
metadata:

```bash
uv run deploy/register_workers.py \
  --plan /data/aberowl/ontologies/worker_plan.json \
  --central http://localhost:8000 --rate-per-min 100 --skip-existing
uv run deploy/fetch_metadata.py /data/aberowl/ontologies --workers 8
```

Registration uses Docker worker names, which the central container resolves on
`aberowl-net`. Check search results after registration and request reindexing
through the administration interface if needed. The download, repair, planning,
launch, registration and metadata helpers also provide separate recovery steps;
`retry_downloads.py` retries failed downloads.

## Verify an installation

```bash
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env ps
curl --fail http://localhost:8000/api/getStats
curl --fail http://localhost:8081/api/health.groovy
python agents/mcp_test_client.py --ontology http://localhost:8766
python scripts/check_api_compat.py http://localhost:8000
```

Use your configured ports. Check classification status, search results and a DL
query for each affected ontology. Compare these checks before and after an
upgrade. A successful health response alone does not establish search or query
correctness. Worker and central logs help diagnose load, indexing and dispatch
failures.

## Back up and upgrade

Before any rebuild or restart, record the deployed commit and container image
IDs. Back up the ontology files, private configuration and the three central
volumes: `deploy_es_data`, `deploy_redis_data` and `deploy_central_config`.
For a consistent filesystem backup, stop the central stack while copying the
volumes; a live Elasticsearch data-directory copy is not a consistent snapshot.
Use Elasticsearch's snapshot facility if the backup must run without stopping it.

For a stopped-volume backup, run from the checkout:

```bash
backup_dir="$PWD/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
git rev-parse HEAD > "$backup_dir/deployed-commit.txt"
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env images > "$backup_dir/images.txt"
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env stop
for volume in deploy_es_data deploy_redis_data deploy_central_config; do
  docker run --rm -v "$volume":/volume:ro -v "$backup_dir":/backup alpine \
    tar czf "/backup/$volume.tar.gz" -C /volume .
done
```

Verify every backup completed, then restart the existing stack with `start` if
not upgrading immediately. Keep backups outside the release archive and protect
them as private data. Record and back up bind-mounted ontology/configuration
files separately.

Update to the reviewed source revision and rebuild only affected services. For
a central-only change:

```bash
docker compose -p deploy -f deploy/docker-compose.central.yml \
  --env-file deploy/.env up -d --build --no-deps central-server
```

This command assumes Redis and Elasticsearch are already running. Restart any
services stopped for backup before applying it. Worker code is bind-mounted by
the bulk launcher; worker code changes require a controlled restart and
classification checks. `rollout_worker.py` provides sequential restart checks;
review its installation assumptions and dry-run output before use.

To roll back code, restore the recorded revision and rebuild the affected
services, or restore the recorded images. To restore data, stop its consumers,
restore verified backups into the matching volumes, then restart and repeat the
installation checks. Keep the old versioned Elasticsearch index when swapping
an alias so an indexing rollback can restore the previous alias target.
`docker compose down -v` removes named volumes and is not an upgrade command.

## Ontology updates

The update pipeline replaces a served ontology with its current upstream
release. It runs only when asked, and only for the ontologies you select.
Automatic source sync and update checks stay off unless `ENABLE_AUTO_SYNC=true`;
leave it off unless you mean to check every selected ontology daily.

Requirements:

- The worker loads the ontology from `/data/<id>/<id>.owl`, the bulk launcher
  layout, and reports that path in `listLoadedOntologies.groovy`. A run checks
  this first and is skipped with `worker_path_mismatch` otherwise.
- Central sees the same tree at `ONTOLOGIES_DIR` (default `/data/ontologies`,
  the central Compose mount) on the same host as the worker. Ontologies stored
  elsewhere, and the self-host stack, which gives central no ontology mount,
  are skipped with `served_file_missing`. Central and workers on different
  hosts need either a verified shared file system for `/data` (mounting the
  right container path is not enough on its own) or, for a worker host with
  its own local directory, the transfer service on that host and an entry for
  the worker in `UPDATE_TRANSFER_CONFIG` (see "Workers on other hosts").
- Workers run the worker code from this change (`validateOntology.groovy`,
  `updateOntology.groovy`, `triggerIndexing.groovy`, the new
  `finalizeUpdate.groovy`, `src/RequestManager.groovy`, `src/ImportCatalogue.groovy`,
  `src/UpdateFiles.groovy` and `docker/scripts/IndexElastic.groovy`). An older
  worker fails validation safely (`worker_outdated` or HTTP 401), so deploy
  workers first, through a controlled restart.
- The registry entry has a `source_url`. BioPortal download links also need
  `BIOPORTAL_API_KEY` in the central environment; never put a key in the URL.
  `deploy/docker-compose.central.yml` passes neither this key nor
  `UPDATE_ONTOLOGIES` or `UPDATE_ARCHIVE_KEEP` into the container; add them to
  its `environment` list if you use them.

Set a source, run one update, and read the outcome. `curl -u admin` prompts
for the password:

```bash
curl -u admin -X PUT -H 'Content-Type: application/json' \
  -d '{"source_url": "https://purl.obolibrary.org/obo/flopo.owl"}' \
  http://localhost:8000/admin/ontology/flopo/source
curl -u admin -X POST http://localhost:8000/admin/ontology/flopo/trigger_update
curl -u admin http://localhost:8000/admin/ontology/flopo/update_status
```

Setting a source marks the entry `source: manual`, which a later source sync
does not override. A trigger is refused with HTTP 409 when the ontology has no
source, is being updated or reindexed, or has an unresolved transaction (see
"Recovery"). The same refusal applies to `PUT .../source`, `POST .../reindex`,
the webhook and batch checks. Add `?wait=true` to receive the outcome in the
response; an update can take as long as classifying the ontology twice, so a
proxy may time out first. `?force=true` swaps in the current release even when
it matches the served one.

`POST /admin/check_updates?ontologies=flopo,po` checks a list and reports each
id as updated, unchanged, failed or skipped (`no_source_url`, `disabled`,
`recovery_required`, `update_in_progress`, `not_registered`). Without
`ontologies`, it checks `UPDATE_ONTOLOGIES` (comma-separated ids) when set, and
otherwise every registered ontology with a source, which may download all of
them. Ontologies on one worker are validated, indexed and swapped one at a
time.

### What a run does

1. Preconditions: a source, a worker serving `/data/<id>/<id>.owl`, an ES
   alias that can be read (an ES error is `es_unavailable`, never "no index"),
   and no unresolved transaction.
2. A HEAD request compares ETag or Last-Modified with the stored values. An
   unchanged source ends the run. Any 4xx or 5xx answer is a failure, except
   405 and 501, which fall back to downloading and comparing MD5.
3. A transaction begins: a unique id `<txn>`, recorded on the registry entry
   as `update_txn` before anything is written.
4. The release is downloaded to `/data/<id>/updates/<txn>/<id>.owl`. If it
   matches the served file (by stored MD5, or by the served file itself on the
   first run), the run ends as unchanged and records the version markers.
5. The worker validates the staged file the way a hot-swap reads it: with the
   ontology's import catalogue if it has one, otherwise with imports
   suppressed, never fetching an import. A catalogue entry that names the
   served file names the staged root. Validation also writes the merged
   ontology (imports merged in, no `owl:imports` left) to the worker's own
   temporary directory, `<java.io.tmpdir>/aberowl-update-exports/<txn>/`,
   because workers mount `/data` read-only.
6. The worker indexes that export into a new index `aberowl_<id>_classes_<txn>`
   and deletes the export when its indexer exits. The indexer now fails on
   any document it cannot write, and central requires the index's document
   count to equal the validated class count.
7. The worker hot-swaps to the staged file. One task per ontology runs on a
   worker at a time; a second is refused with HTTP 409.
8. The served file is copied to `/data/<id>/archive/<id>_<UTC time>.owl` (the
   newest `UPDATE_ARCHIVE_KEEP`, default 3, are kept) and atomically replaced
   by the staged content. The worker is then told the served path
   (`finalizeUpdate.groovy`), so it no longer reports the staging path, and
   the staging directory is deleted.
9. The search alias moves to the new index. The index it pointed to is kept
   for rollback (`previous_es_index`); the one kept by the run before is
   deleted. An index the alias points to is never deleted.
10. The registry records the version markers, `update_status`,
    `last_check_status`, `update_history` and import changes, and the
    transaction record is removed.

A reindex (`POST .../reindex`) runs steps 6 and 9 on the served file, with
the same transaction record, the same index retention and the same handling
of unknown outcomes. It indexes with the standalone indexer's import handling
(see "Imports").

### Workers on other hosts

When a worker's host keeps its own ontology directory, central's downloads and
promotions never reach it. Run the transfer service (`transfer_service/`) on
that host and map the worker to it in a file named by `UPDATE_TRANSFER_CONFIG`
in central's environment (`deploy/docker-compose.central-transfer.yml` sets it
and mounts the mapping); `transfer_service/README.md` has the setup,
configuration, token handling, recovery and rollback steps. For a mapped
worker, steps 1, 2, 4 and 8 change:

- Step 1 also reads the served file's SHA-256 and MD5 on the worker's host
  through the service, and skips with `transfer_unreachable`,
  `transfer_refused` or `served_file_missing` when it cannot. Central's own
  copy is not read for any decision. A stage the previous update could not
  remove (`transfer_release_pending`) is removed first, once the worker
  reports the served path and the host still serves that release; otherwise
  the run is skipped with `transfer_cleanup_pending`. An unfinished
  transaction on the host that central no longer tracks is skipped with
  `transfer_transaction_open`.
- Step 2 ends the run as unchanged only when the worker's host still serves the
  file of the last complete update (its MD5 matches the stored one) and
  central's own copy, if it keeps one, holds it too; otherwise it downloads.
- Step 4 ends the run as unchanged only when the download is the release of
  the last complete update and the worker's host serves it. If the host
  already serves the download but the registry recorded another release, the
  update runs in full with identical bytes, so search, the registry and
  dependents catch up. An unchanged run also refreshes central's own copy
  when it differs; a refresh that fails is reported in `update_warnings` and
  retried by the next check.
- Step 4 downloads into central's `ONTOLOGIES_DIR/.update-downloads/<id>/<txn>/`
  and uploads the release to the service. The service checks that the served
  file still has the SHA-256 central read (compare and set), verifies the size
  and SHA-256 of the upload, and only then places it at
  `/data/<id>/updates/<txn>/<id>.owl` on the worker's host. Validation,
  indexing and the hot-swap read it there from the read-only mount, as before.
- Step 8 asks the service to archive the served file as
  `/data/<id>/archive/<id>_<txn>.owl` and replace it, again only while it
  holds the SHA-256 central read, then reads the served file's SHA-256 back
  before telling the worker the served path. Version markers are recorded only
  after that. The service removes the stage after `finalizeUpdate.groovy`; if
  it does not confirm, the update still succeeds, with a warning and
  `transfer_release_pending` for the next check. Central replaces its own copy
  of the served file, if it has one, with the release. Ontologies whose import
  catalogue names the file are found in the worker host's `import_catalogues/`.

An outcome the service cannot confirm is a recovery case like any other: the
transaction record stays, the stage on the worker host is kept, and the
ontology and its worker are blocked as described under "Recovery". Workers
not in the mapping keep the shared-directory behaviour, deliberately; a
`worker_url` that does not match the registry's `url` therefore shows up as a
validation failure, not as a configuration error. A mapping file that is set
but unusable stops every update (`transfer_config_invalid`) rather than
falling back to it.

In both modes, an ontology without a recorded MD5 counts its served file as
current only if no run has stopped for recovery since its last complete
update.

### Failures

`last_check_status` is `updated`, `unchanged`, `skipped`, `failed`,
`recovery_required` or `resolved_by_operator`. `update_status` names the step
that failed: `check_failed`, `source_gone`, `download_failed`,
`validation_failed`, `indexing_failed`, `hotswap_failed`, `reindex_failed` or
`recovery_required`. `update_error` carries the worker's message. A skip
changes only `last_check_status`. An ontology paused with `/disable` stays
paused after a manual run. Version markers (ETag, Last-Modified, MD5) change
only when an update completes, so a run that stopped part way is never taken
for "unchanged" later.

A failure whose state is known ends the transaction and removes what the run
created and nothing still uses: the staging directory, the new index and the
export. This covers failures before the hot-swap, a refused task, a failed
indexing task, an incomplete index, and a failed hot-swap. For the hot-swap,
the worker reports whether the previous copy is still serving. With an import
catalogue it usually is, because the new copy is read before the old one is
unloaded. Without a catalogue the worker unloads first, so the pipeline asks
it to reload the unchanged served file. The ontology is unavailable until
that reload ends.

### Recovery

When the outcome is unknown or the state is inconsistent, the run stops,
marks the transaction `recovery_required` with its phase and error, and keeps
everything: staging directory, new index, export and archive. Every later
update, reindex, source change, webhook and batch check of that ontology is
refused until the transaction is resolved. A central restart does not clear
it; a record still in state `running` after a restart blocks the same way.
The cases:

| Error | Phase | What is known |
|---|---|---|
| `indexing_unknown` | index | An indexing task timed out, was unknown to the worker (404), or its start got no answer. It may still be writing the new index. |
| `hotswap_unknown` | hotswap | The same for the hot-swap. The worker may be serving either release, or neither. |
| `restore_failed` | restore | The swap failed, the worker had unloaded the ontology, and reloading the served file failed too. The ontology is probably offline. |
| `promote_failed` | promote | The worker serves the new release from the staging file; the served file still holds the old one. A restart would load the old one. |
| `finalize_failed` | finalize | The served file holds the new release; the worker still records the staging path, which still exists. |
| `alias_swap_failed` | alias | The worker and the served file hold the new release; search still uses the old index. |
| `es_unavailable`, `unexpected`, `cancelled` | any | Elasticsearch failed, central hit an error, or the run was cancelled, in the phase shown. |
| `transfer_unknown`, `transfer_cleanup_unknown`, `promote_unknown` | transfer, promote | Workers on other hosts only: the transfer service could not confirm the stage, its removal, or the replaced served file. See `transfer_service/README.md`, "Recovery". |

To reconcile one, read `GET /admin/ontology/<id>/update_status`. Its
`update_txn` names the worker, task id, staging path, index and export. Then:

1. Check the worker task: `GET <worker>/api/updateStatus.groovy?taskId=<id>`.
   While it is `pending`, wait. A 404 means the worker does not know the task,
   for example after a restart. It does not tell you the outcome.
2. Check what the worker serves: `GET <worker>/api/listLoadedOntologies.groovy`
   (its `path` and `status` for the ontology) and a query.
3. Bring the three parts into agreement, using the archived and staged files:
   - The served file: `/data/<id>/<id>.owl`.
   - What the worker serves: a controlled worker restart loads the served
     file. Alternatively, call `finalizeUpdate.groovy` with action
     `finalize`, `owlPath` set to the staging path and `servedPath` set to
     the served file, once the served file holds the release the worker
     serves.
   - The search alias: move it with ES `POST /_aliases`, or rebuild the
     index with `/reindex` after resolving.
4. Remove what is no longer needed. Delete the staging directory, and the new
   index unless the alias points to it. A leftover export is under the
   worker's temporary directory; `finalizeUpdate.groovy` with action
   `discardExport` removes it. For a worker on another host, the staging
   directory is on that host: use the transfer service's `abort` or `finalize`
   (`transfer_service/README.md`), which also closes its own record, and
   remove central's download under `ONTOLOGIES_DIR/.update-downloads/<id>/<txn>/`.
5. Resolve the record. This deletes nothing, and is refused while the task is
   still `pending`:

   ```bash
   curl -u admin -X POST -H 'Content-Type: application/json' \
     -d '{"txn_id": "<txn>", "reconciled": true, "note": "what was found and done"}' \
     http://localhost:8000/admin/ontology/<id>/resolve_update
   ```

The next update checks the source again against the last complete update's
markers, so a release that was half applied is applied again in full.

### Beta rollout

1. Back up as in "Back up and upgrade", and record the deployed commit.
2. Deploy the worker code to the worker serving the test ontology, through a
   controlled restart. Check that `listLoadedOntologies.groovy` reports
   `/data/<id>/<id>.owl` for it, and run the slow servlet tests against a test
   stack if one is available.
3. Deploy central. Leave `ENABLE_AUTO_SYNC` off.
4. Update one ontology with the commands above, follow `update_status`, and
   verify the worker, the served file, the archive, the alias and the
   previous index, and search and queries.
5. Repeat the trigger: it must report `unchanged`.
6. Only then select further ontologies, a few at a time.

A beta whose central and worker share one directory does not exercise the
transfer service. Before relying on it for workers on other hosts, repeat
these steps with a mapped worker whose host has its own directory: check the
served file's SHA-256 through the service before and after, that the stage
appeared on the worker host and was removed after finalize, the archive on the
worker host, a worker restart (it must load the new release from
`/data/<id>/<id>.owl`), and that no other file on that host changed.

### Queued update execution

`UPDATE_EXECUTION` decides where updates, reindexes and batch checks run:

- `inline` (the default, set explicitly in `docker-compose.central.yml`): in
  the public `central-server` process, as before. It is supported only with
  one API process and no update runner.
- `queued`: `central-server` only records a job in Redis and answers with its
  id; the `update-runner` service claims and runs it. Use
  `deploy/docker-compose.central-queued.yml`, which sets the mode for both
  services.

Any other value, or an invalid `UPDATE_*` setting listed below, refuses every
update request with HTTP 503 and stops the runner. Nothing falls back to
running inline.

In both modes exactly one heavy job runs at a time: one update, one reindex,
or one batch check, whose ontologies run one after another. The limit covers
every trigger: `trigger_update`, the webhook, `check_updates`, `reindex` and
the scheduled check. It covers download, hashing, validation, indexing,
hot-swap and their recovery. It does not cover interactive queries, such as DL
queries, which the workers answer as before. Inline mode refuses a second
heavy job with HTTP 409. Queued mode queues it.

The job that runs holds a global slot in Redis until its outcome is known. A
known outcome releases the slot: success, unchanged, a skip, or a failure that
was cleaned up. An unknown outcome keeps the slot, and no further heavy job
starts anywhere. Unknown outcomes include an `update_txn` left
`recovery_required`, an error outside the pipeline's own handling, and a
runner that crashed, was stopped during a job or lost Redis. Nothing expires
the slot. A batch stops at such an ontology; the ids it did not reach are
listed under `not_run`. While any registry entry has an `update_txn`, the
runner claims nothing.

Starting central or the runner never queues work. The runner runs only jobs
that were deliberately queued: by an operator, a webhook, or the scheduled
check, which still needs `ENABLE_AUTO_SYNC=true`. In queued mode the scheduled
check is queued at most once per `UPDATE_CHECK_INTERVAL_SECONDS`, across
restarts and API processes.

#### Requests and status

In queued mode `trigger_update`, `reindex` and `check_updates` answer HTTP 202
with `job_id`. A request equal to a job that is still queued or running
returns that job with `duplicate: true`. `check_updates` fixes its selection
when it is queued. With `?wait=true` the request waits up to
`UPDATE_WAIT_SECONDS` (default 25). A job that ended in that time answers
HTTP 200 with the same body as inline mode, plus `job_id` and `job_state`.
Otherwise the answer is HTTP 202 with the job's state, which is neither
success nor failure; the job goes on, and closing the connection does not
cancel it.

```bash
curl -u admin http://localhost:8000/admin/update_jobs
curl -u admin http://localhost:8000/admin/update_jobs/<job_id>
curl -u admin -X POST http://localhost:8000/admin/update_jobs/<job_id>/cancel
```

`/admin/update_jobs` shows the mode, the holder of the global slot (`active`
for a queued job, `inline` for an inline run) with its `claim`, the queue, the
most recent ended jobs (`UPDATE_JOB_RETAIN`, default 200, are kept) and each
runner's last heartbeat. `update_status` of an ontology shows `update_job`
when the slot's holder concerns it. Cancel removes a queued job, or stops a
running batch before its next ontology; a running update or reindex cannot be
cancelled. Status and job records hold no registry secrets or tokens.

Settings, all optional: `UPDATE_QUEUE_PREFIX` (default
`aberowl:update_jobs`; one namespace per deployment), `UPDATE_WAIT_SECONDS`,
`UPDATE_QUEUE_MAX` (queued jobs accepted, default 100),
`UPDATE_JOB_RETAIN`, `UPDATE_RUNNER_HEARTBEAT_SECONDS` (default 15),
`UPDATE_RUNNER_STALE_SECONDS` (default 120, at least three heartbeats) and
`UPDATE_RUNNER_POLL_SECONDS` (default 5). `REDIS_URL` (default
`redis://redis`) is used by central and the runner alike. The queued override
gives both services the same prefix, heartbeat, stale period and download
limits from the env file. Set them there, never for one service only: a
different prefix splits the two silently, and `GET /admin/update_jobs` marks
a runner whose other settings differ with `settings_match: false`. Runner
entries silent for a day are removed from the list.

In queued mode `PUT .../source`, `/disable` and `/enable` are refused (409)
while any queued, running or held job covers the ontology; cancel a queued
job first. The check and the change are one Redis transaction. A batch
covers every ontology it selected, including those it has not reached. To
change one of them, cancel the batch, which stops before its next ontology,
wait until it ends, make the change, then submit the ids listed under its
`not_run` with `check_updates?ontologies=...`. Every other
registry write of the API (the status poll, source sync, metadata
fallbacks, registration and `resolve_update`) is a transaction on the newest
entry, so it never overwrites what the runner wrote meanwhile.
`POST /api/update_result`, a worker callback the pipeline no longer uses, is
refused in queued mode. The reverse is not guarded: the runner's own writes
read the entry first, so an API change landing a few round trips before one
of them can be lost. Change a worker's URL, or re-register a worker, only
while the runner is idle.

#### Start, stop and back up

Switch from inline to queued mode while nothing runs:

1. Read `GET /admin/update_jobs` and every ontology's `update_status`. No
   inline run may hold the slot, and no `update_txn` may be left; reconcile
   them first as described under "Recovery".
2. Back up as in "Back up and upgrade". The Redis volume holds the queue and
   job history with the registry.
3. Start both services with the override, as shown in its header. With
   workers on other hosts, add `docker-compose.central-queued-transfer.yml`
   and leave out `docker-compose.central-transfer.yml`.
4. Check that `GET /admin/update_jobs` reports `mode: queued` and a fresh
   runner heartbeat. Then update one ontology, as in "Beta rollout".

To stop the runner, wait until `GET /admin/update_jobs` shows no running job,
or cancel a running batch and wait for it to end, then run
`docker compose ... stop update-runner` with the same files. A stop during a
job cancels it: the pipeline records its transaction for recovery and the
job becomes `recovery_required`. Jobs queued meanwhile stay queued in Redis
and run when a runner starts. To return to inline mode, stop the runner while
idle, cancel or let finish every queued job, and deploy without the override.

#### Recovering the global slot

When `GET /admin/update_jobs` shows a holder in state `recovery_required`, or
in state `running` with no fresh heartbeat from its runner:

1. For a holder still `running`, stop its runner (or, for an inline run, the
   API process that ran it) and wait `UPDATE_RUNNER_STALE_SECONDS`.
2. Reconcile and resolve each ontology transaction it left, as under
   "Recovery"; `resolve_update` refuses while a worker task is pending.
3. Release the slot, with the claim from the status:

   ```bash
   curl -u admin -X POST -H 'Content-Type: application/json' \
     -d '{"claim": "<claim>", "reconciled": true, "runner_stopped": true, "note": "what was found and done"}' \
     http://localhost:8000/admin/update_jobs/<job_id>/recover
   ```

The release is refused unless the claim matches, while the runner still sends
heartbeats, and while a transaction the job left is unresolved. A holder still
`running` is first marked `recovery_required`, and the answer lists the
transactions to resolve; call it again afterwards. `resolve_update` is
refused while a job in any process is running on that ontology.

What a released runner can still do. The runner's registry writes apply only
while its job holds the slot with its claim (checked in the same Redis
transaction). Before every other step (a worker request, creating or deleting
an index, moving the alias, replacing or removing a file) and at every phase
boundary, it checks the claim, and its heartbeat checks it too. It also stops
its job when Redis has been unreachable for half of
`UPDATE_RUNNER_STALE_SECONDS`; a Redis that hangs counts too, because the
runner's Redis commands time out after one heartbeat interval. A check that
fails or times out stops the job before its next step. A heartbeat that fails
in any other way stops the runner. Service Redis only while the runner is
idle: an outage during a job cancels it, its worker task runs on, and the job
then needs recovery. A runner that finds its claim gone writes
nothing more and exits with status 3. This does not undo a request already
sent or a worker task already started. A check and the step after it are
one round trip apart, and a paused process checks nothing until it resumes.
That is why the release needs the runner stopped and silent, and the state
reconciled. Stop the runner container; do not merely pause it.

#### Downloads

Every mode streams a download to disk and removes gzip as it arrives, whether
the server sent `Content-Encoding: gzip` or the file is a `.gz` (at most two
layers, recognised by their content). Concatenated gzip members are read in
turn; other data after the stream, a truncated or corrupt stream, another
`Content-Encoding`, and a body that does not match its `Content-Length` fail
the download. `UPDATE_MAX_DOWNLOAD_BYTES` (default 4 GiB) bounds the bytes
received and `UPDATE_MAX_EXPANDED_BYTES` (default 8 GiB) the file written. The
file reaches the staging path only when complete; a failed download removes
only its own partial file. Each limit must be a whole number of bytes from 1
to 1 TiB; any other value refuses every update (HTTP 503) and stops the
runner. The queued override passes both limits to both services; in inline
mode add them to central's environment if you change them.

### Imports

An import catalogue (`/data/import_catalogues/<id>.json`) and the module files
it lists are operator-managed. Updates never change them, and validation and
the hot-swap read them as they are on disk. They are read twice in a run, at
validation and at the hot-swap. Changing them in between makes the two reads
differ.

- If a new release imports something its catalogue does not list, validation
  fails, the error names the import IRI, and the served copy stays. Add the
  module file and catalogue entry, then trigger again.
- The registry keeps the root's declared imports (`source_declared_imports`).
  When a release adds or removes one, `imports_changed` records it, also for
  ontologies without a catalogue.
- After an ontology is updated, central lists the ontologies whose catalogue
  names its file (`catalogue_dependents`) and marks each with `stale_imports`.
  They keep their merged copy of the old release until they are reloaded. They
  are not reloaded automatically. `trigger_update?force=true` on a dependent
  reloads it and clears the mark. Catalogues that reach the file through a
  symlink, and module files replaced by hand, are not detected.
- An update indexes the validation export, so its search index holds the
  classes the worker classifies, imports included only through a catalogue,
  and nothing is fetched. For an ontology without a catalogue that declares
  imports, this means its imported classes are no longer indexed under it
  after an update, where the standalone indexer used to fetch them.
- A reindex, and indexing outside the update pipeline, still run the
  standalone indexer (`IndexElastic.groovy`) on the served file with OWLAPI's
  default import handling. That may fetch import IRIs over the network, and
  its search content can differ from what an update indexes. Changing that is
  deferred.

## Registry maintenance

Per-ontology registration/webhook keys differ from `ABEROWL_SECRET_KEY`.
`scripts/rotate_registry_keys.py` rotates registry keys and synchronizes the
cold-start seed. Back up Redis and the seed first; review self-registration,
webhook consumers and worker-auth fallback before rotation. Run inside the
central container, first in dry-run mode, then with `--apply` when ready:

```bash
docker cp scripts/rotate_registry_keys.py deploy-central-server-1:/tmp/
docker exec deploy-central-server-1 python3 /tmp/rotate_registry_keys.py
```

`scripts/repoint_ontology.py` moves registry entries to a different worker while
preserving their keys. It edits Redis directly. Use its dry-run mode, confirm the
ontology has loaded on the destination, then apply and verify queries through
the central API. Keep these maintenance operations separate from code upgrades.

Validation timeouts are also uncertain outcomes: their staged input is retained. An unresolved transaction involving a worker blocks heavy update/reindex work for other ontologies on that worker too, including after central restarts. Confirm the parser or task has stopped before resolving the record; a validation without a task id can require a controlled worker restart. Other workers remain available for updates.
