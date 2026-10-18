# Ontology transfer service

Central's ontology update pipeline downloads a release, has the worker
validate and index it, hot-swaps the worker onto it and then promotes it to
the served file `/data/<id>/<id>.owl`. By default it does this in a directory
that central and the worker share. When a worker runs on another host with its
own local ontology directory, central's files never reach it, and the worker
mounts its directory read-only, so it cannot write them either.

This service fills that gap. It runs on the worker's host, outside the worker
containers, and is the only process that writes the host directory the workers
mount at `/data`. Central talks to it over HTTPS with a bearer token. It is
opt-in: nothing changes for a worker that is not mapped to a transfer service.

The service:

- writes only inside the directories of the ontologies it is configured for,
  and only paths it derives from the ontology id and central's transaction id;
- has no access to the workers, Docker, a shell or SSH, and does not run as root;
- keeps a journal outside the ontology directory, so an interrupted operation
  is completed or refused when it is asked again;
- never deletes anything on its own; it removes a transaction's own files only
  when asked.

## What happens during an update

Central keeps its transaction record (`update_txn`) as before. For a mapped
worker the file steps change:

| Pipeline step | Shared directory (default) | Transfer service |
|---|---|---|
| Preconditions | central's `ONTOLOGIES_DIR/<id>/<id>.owl` must exist | `GET .../served`: the file on the worker's host, its SHA-256 and MD5, and the host's open transactions |
| Unchanged checks | the recorded MD5 of the last complete update | the same, and the worker host's file must hold that release too; an unchanged ETag or Last-Modified also needs central's own copy to hold it |
| Download | into `/data/<id>/updates/<txn>/` | into central's `ONTOLOGIES_DIR/.update-downloads/<id>/<txn>/` |
| Stage | (already there) | `PUT .../transactions/<txn>` then `PUT .../file`: same path `/data/<id>/updates/<txn>/<id>.owl` on the worker's host |
| Validate, index, hot-swap | unchanged | unchanged; the worker reads the staged path from its read-only mount |
| Promote | central archives and replaces the served file | `POST .../promote`, then central reads the served file's SHA-256 back |
| Finalize | `finalizeUpdate.groovy` | unchanged |
| Clean up | central removes the staging directory | `POST .../finalize` removes the stage; central refreshes its own copy of the served file |

Import resolution, the import catalogue, the validation export and indexing
stay on the worker exactly as before. The export is still written to the
worker's own temporary directory.

If the worker host already serves the downloaded release but the registry's
last complete update was another one (for example after a run that stopped
after promotion and was resolved), the update runs in full with identical
bytes, so that search, the registry and dependents follow. Without any
recorded MD5 (a source configured for the first time) the host's file counts
as current only if no run has stopped for recovery since the last complete
update.

### Protocol

All endpoints are under `/v1` and need `Authorization: Bearer <token>`. The
token is checked from the headers before any body is read; JSON bodies are
limited to 16 KiB. `<id>` must be listed in the configuration; `<txn>` is
central's transaction id (lower-case letters, digits and hyphens).

| Request | Effect |
|---|---|
| `GET /v1/ontologies/<id>/served` | SHA-256, MD5 and size of `<id>/<id>.owl`, its worker path, open transactions, and the ontologies whose import catalogue on this host names it. |
| `PUT /v1/ontologies/<id>/transactions/<txn>` `{"sha256", "size", "base_sha256"}` | Prepare. Refused unless the served file's SHA-256 is `base_sha256` (409 `base_mismatch`), no other transaction of `<id>` is open (409 `other_transaction_open`; a promoted one stays open until finalized), the size is within `max_bytes` (413) and enough space remains (507). Repeating it with the same values answers the existing record. |
| `PUT /v1/ontologies/<id>/transactions/<txn>/file` | Upload exactly `size` bytes. They go to a private temporary file; only when size and SHA-256 match does it become the stage, by a link that never replaces a file, and the journal records it as staged. A short, long or corrupted body (400, 413, 422) or a dropped connection leaves no stage. |
| `GET /v1/ontologies/<id>/transactions/<txn>` | The journal record, the stage and served files as they are, and `observed`: `prepared`, `staged`, `promoted`, `finalized`, `aborted` or `conflict`. A complete stage whose journal entry was interrupted is recorded as staged first. |
| `POST .../promote` `{"sha256", "base_sha256"}` | Archive the served file as `<id>/archive/<id>_<txn>.owl` and atomically replace it with the stage, only while it still has `base_sha256` (409 `served_changed` otherwise). The stage is kept, because the worker reads it until central finalizes. Repeats are answered from the journal and the files. No space for a copy is 507, a definite refusal. |
| `POST .../finalize` `{"sha256"}` | Remove the stage of a promoted transaction, once the served file holds its release, and close the transaction. Then remove the archives of older finished transactions beyond `archive_keep`. Operators only: `"served_changed_ok": true` closes a promoted transaction whose served file was since changed by hand (see "Recovery"). |
| `POST .../abort` | Remove the stage of a transaction that was never promoted. A promotion is never undone here (409 `cannot_abort`). |
| `GET /health` | `{"status": "ok"}`, no token, no details. |

Promotion is journalled as `promoting` before any file changes. The archive
and the stage are hard links where the file system allows it (the served file
and the stage are then the same file), and copies verified by SHA-256
otherwise. The identity (device and inode) of the file about to replace the
served file is journalled before the replace. If the service stops part way,
the next request finds the served file either still the base (promotion
continues) or the journalled file (promotion is confirmed); equal content
alone does not confirm a promotion whose release equals its base. Anything
else is a `conflict`, and nothing is overwritten.

Central retries a request that got no answer at most three times, and reads
the transaction's status between attempts: an upload whose answer was lost is
not sent again, and a promotion whose answer was lost is read back from the
served file. A stage counts as staged only once the journal says so. If the
status cannot be read, the transaction is left for recovery.

## Set up a worker host

1. Pick the host directory the workers mount read-only at `/data` (for the
   bulk launcher, `ONTOLOGIES_PATH`). Choose a dedicated user (uid and gid)
   that owns it, including every configured ontology directory: the service
   refuses to start when it cannot write one. Create a state directory outside
   it, owned by the same user. Neither may be reached through a symlink.
2. Create a token of at least 32 random characters, for example
   `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`, in a file
   owned by that user with mode 0400. Docker Compose bind-mounts the file, so
   it must be readable by `TRANSFER_UID` on the host. Use a different token per
   worker host.
3. Write the configuration from `deploy/transfer-config.example.json`.
   `data_root` and `state_dir` are paths as the service sees them (inside the
   container for Docker: `/srv/aberowl/ontologies` and
   `/var/lib/aberowl-transfer`). List only the ontologies to be updated through
   this host. Unknown keys, a missing or unwritable ontology directory, a weak
   token or a state directory inside the data root stop the service at start.
4. Provide a TLS certificate (`tls.crt`, `tls.key`) for the address central
   uses. A certificate from a private CA is fine; give central the CA file. The
   image serves HTTPS by default.
5. Start it, either with Docker:

   ```bash
   docker compose -p aberowl-transfer -f deploy/docker-compose.transfer.yml \
     --env-file deploy/transfer.env up -d --build
   ```

   (the variables are listed at the top of the Compose file; `TRANSFER_BIND`
   has no default and must be a private address of the host), or with systemd,
   using `deploy/aberowl-transfer.service` as a template. Publish the port on a
   private address that central can reach and nothing else can; never on all
   interfaces, and never through the public reverse proxy.
6. Check it from central's host:

   ```bash
   curl --cacert ca.pem https://<transfer-host>:18443/health
   curl --cacert ca.pem -H "Authorization: Bearer $(cat token)" \
     https://<transfer-host>:18443/v1/ontologies/flopo/served
   ```

   The SHA-256 must be that of the file the worker serves.

Plain HTTP is accepted only when central's mapping says
`"allow_insecure_http": true` and the service is started with a command
without the TLS options. Use it only on a network you trust end to end, such
as an encrypted tunnel between the hosts; the token travels in every request.

## Configure central

1. Write a mapping from `deploy/update-transfers.example.json`. `worker_url` is
   the worker URL in the registry (the `url` of the ontology's entry, compared
   without case or trailing slash). Give the token with `token_file` or with
   `token_env`, the name of an environment variable, and the CA with
   `ca_file`. Paths are as the central container sees them.
2. Put the mapping (named `transfers.json`), the token files and the CA file in
   one host directory and set `UPDATE_TRANSFER_DIR` to it in central's private
   env file. Start central with the override, which mounts it read-only at
   `/run/aberowl-transfer` and sets `UPDATE_TRANSFER_CONFIG`:

   ```bash
   docker compose -p deploy -f deploy/docker-compose.central.yml \
     -f deploy/docker-compose.central-transfer.yml --env-file deploy/.env \
     up -d --no-deps central-server
   ```

Workers not listed keep the shared-directory behaviour; that is deliberate,
so a mixed installation needs no extra configuration. A worker URL that does
not match its registry `url` is therefore not an error: its updates use the
shared directory and, on a split host, fail validation with
`owl_validation_failed` ("owlPath must be an existing regular file"). If you see
that for a host you meant to map, compare the two URLs. If the variable is
set but the file is unusable (unreadable, unknown keys, plain HTTP without
the flag, a weak token, a worker listed twice), every update is skipped with
`transfer_config_invalid`; nothing falls back to the shared directory.
Reindexing does not move files and is not affected.

## The token

The bearer token is write authority over the configured ontologies on that
host, by design: whoever holds it can make any bytes the served file of a
configured ontology (the service does not check that a worker validated
them; central does that before it asks), and by running enough transactions
can cause the archives of older ones to be pruned. It cannot reach any other
path, ontology or host. Treat it like a deployment credential:

- Only central and the service hold it, in files of mode 0400, one token per
  worker host. It never appears in the registry, the URL or the logs.
- Rotate it with no update running (`update_status` shows no `update_txn` and
  `in_progress` false): write the new token to the service's token file and to
  central's, restart the service, then central. Until both have restarted,
  update checks of that host are skipped (`transfer_refused` or
  `transfer_unreachable`); nothing is changed.
- If it may have leaked, stop the service first (that stops every write), then
  rotate. For each configured ontology compare the served file's SHA-256 with
  the registry (`update_history` records `sha256` for remote updates) and look
  for journal records (`<state_dir>/journal/<id>/`) that no central
  transaction explains. Restore from your own backups where needed: the
  archives live on the same host, writable by the same service, and are not a
  backup against a token holder.
- Archives are hard links or copies of former served files with the same
  owner and mode, inside `/data/<id>/archive/`, readable by the workers like
  any file under `/data`.

## Recovery

Central records every unknown or inconsistent outcome as before
(`update_txn`, `recovery_required`) and refuses further updates of that
ontology, and heavy work on that worker, until an operator resolves it. With
remote transport the record also carries `transport: "remote"`, `transfer`
(the service URL), `base_sha256` and `sha256`. The additional errors:

| Error | Phase | What is known |
|---|---|---|
| `transfer_unknown` | transfer | The service could not be reached, or could not confirm the stage. Nothing was validated or swapped. |
| `transfer_cleanup_unknown` | any before the swap | The run failed with a known state, but the service did not confirm removing the stage. |
| `promote_failed` | promote | The worker serves the release from the stage; the service refused to replace the served file: it changed (`served_changed`), or there was no space for a copy (`insufficient_space`). |
| `promote_unknown` | promote | The worker serves the release from the stage; whether the served file was replaced could not be read back. |

Two skips name work left on the host. Neither starts a download:

| Skip | Meaning |
|---|---|
| `transfer_cleanup_pending` | The previous update succeeded, but the service did not confirm removing its stage (`transfer_release_pending` on the registry entry). Each check retries the removal first, once the worker reports the served path and the host still serves that release; the update then proceeds. If the host serves something else, the removal is left to you. |
| `transfer_transaction_open` | The service has an unfinished transaction of the ontology that central no longer tracks, typically after a recovery was resolved without closing it on the service. |

To reconcile, read the service's view of the transaction:

```bash
curl --cacert ca.pem -H "Authorization: Bearer $(cat token)" \
  https://<transfer-host>:18443/v1/ontologies/<id>/transactions/<txn>
```

and compare `observed`, `stage_file` and `served_file` with what the worker
reports (`listLoadedOntologies.groovy`: its `path` for the ontology). Then:

- `observed: "staged"` or `"prepared"`, nothing pending on the worker, and the
  worker reports `/data/<id>/<id>.owl`: `POST .../abort`.
- `observed: "staged"` and the worker reports the stage path: if the served
  file is still the base, `POST .../promote` with the record's `sha256` and
  `base_sha256`; then call `finalizeUpdate.groovy` (action `finalize`) on the
  worker, check that it now reports `/data/<id>/<id>.owl`, then
  `POST .../finalize` with `{"sha256": ...}`.
- `observed: "promoted"` and the worker still reports the stage path: call
  `finalizeUpdate.groovy` first, check the reported path, then
  `POST .../finalize`. Never remove a stage the worker still reports: a
  restart would then fail to load the ontology.
- `observed: "promoted"` and the worker reports the served path:
  `POST .../finalize`.
- `observed: "conflict"`: someone changed the files. Decide by hand which
  release should be served, using the stage and the archive, and restart the
  worker on it. For a promoted transaction, once the worker reports the served
  path, close it with `{"sha256": ..., "served_changed_ok": true}`. For one
  that never promoted, `POST .../abort`. Only if neither applies, move the
  journal record (`<state_dir>/journal/<id>/<txn>.json`) out of the journal
  directory while the service is stopped.

Then resolve central's record with `POST /admin/ontology/<id>/resolve_update`
as described in `deploy/README.md`, and remove central's download under
`ONTOLOGIES_DIR/.update-downloads/<id>/<txn>/`; resolving deletes nothing.
The next update compares with the last complete one, so a release that was
promoted but not completed is applied again in full.

## Roll back

- To stop using the service, remove the worker from central's mapping (or
  start central without the override) and restart central, after every open
  transaction is closed (`GET .../served` lists `open_transactions`). Updates
  of that worker are then skipped (`served_file_missing`) or fail validation
  safely, as before.
- To restore a previous release of an ontology:
  1. Disable its updates (`POST /admin/ontology/<id>/disable`), keep
     `ENABLE_AUTO_SYNC` off, and do not trigger it by hand. With remote
     transport the next update would otherwise apply the upstream release
     again, because the host no longer serves the recorded one.
  2. Check that no transaction is open on the service and none on central.
  3. On the worker host, as the service's user, replace the served file in one
     rename, so the worker never reads a partial file:

     ```bash
     cd <ontology directory>/<id>
     cp --preserve=mode archive/<id>_<txn>.owl .<id>.owl.rollback
     mv -T .<id>.owl.rollback <id>.owl
     ```

  4. Restart the worker through a controlled restart, and check that it
     reports `/data/<id>/<id>.owl` and the expected classes.
  5. Point the search alias back at `previous_es_index` if that index holds
     the restored release, or reindex after re-enabling is planned.
- Stopping the service changes no file. Its journal and the archives remain.

## Limits

- One transaction per ontology at a time on a host, open from prepare until
  finalize or abort, and central's per-ontology and per-worker locks as before.
  The lock is a file lock in the state directory, so only processes sharing
  that directory exclude each other: run one service per ontology directory.
- The update is not atomic across the worker, the files and the search index.
  Each step is verified and a failure stops the run for recovery, but nothing
  rolls back the other parts automatically, and the ontology may be briefly
  unavailable during a hot-swap as before.
- The served file is checked for changes by SHA-256 just before promotion and
  by inode, size and modification time immediately before the replace. A
  writer that bypasses the service in that window, or edits a file in place
  without changing its size or modification time, is not detected.
- The service cannot tell whether a worker still reads a stage; central
  removes one only after the worker reported the served path (at finalize, or
  at the next check for a kept stage), and aborts only after a known failure.
- A recovery in the transfer phase quarantines the whole worker, like any
  other unresolved transaction on it, although the transfer did not use the
  worker. This is deliberately conservative.
- Ontology directories, archives and stages must be on one file system for
  hard links; otherwise the service copies, which needs free space for an
  extra copy of the file.
- `catalogue_dependents` are read from the host's `import_catalogues/`; a
  catalogue that reaches a file through a symlink is not detected.
- Central's own copy of the served file is refreshed only when it already
  exists: after an update, and by any later check that finds it differs from
  the recorded release (a failed refresh is reported in `update_warnings` and
  retried). Central's archive directory is not used for remote workers.

The Compose example publishes private host port 18443 to container port 8090. Set `TRANSFER_PORT` to another unused port if needed and use the same port in central’s mapping. Check the host’s existing listeners before starting the service.

### Clearing a reconciled central cleanup marker

Central first reads the service journal when retrying `transfer_release_pending`. If the journal already says `finalized`, central clears its marker even after an operator rollback. Other cases remain blocked rather than deleting uncertain files. A changed transfer mapping is also blocked.

If the journal is absent, the service has moved, or automatic reconciliation cannot establish the state, reconcile the worker and files first. Then use central's authenticated admin endpoint `POST /admin/ontology/<id>/resolve_update` with `{"txn_id":"<pending transaction>","reconciled":true,"clear_transfer_cleanup":true,"note":"<what was checked and resolved>"}`. The transaction ID must match the cleanup marker and a non-empty note is required. Resolve any `update_txn` first. This action records an audit event and clears only the central marker: it does not remove staging files, close service transactions or change the served file. Keep automatic updates disabled during rollback.
