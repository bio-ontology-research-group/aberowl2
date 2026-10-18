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
  hosts need a verified shared file system for `/data`; mounting the right
  container path is not enough on its own.
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
   `discardExport` removes it.
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
