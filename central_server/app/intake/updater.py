"""
Ontology update pipeline.

Updates a single ontology from its upstream source, or reindexes the file it
is served from. File layout, as the worker sees it (central sees the same
tree under ONTOLOGIES_DIR):

  /data/<id>/<id>.owl                    the served file the worker config loads
  /data/<id>/updates/<txn>/<id>.owl      a download being validated and swapped in
  /data/<id>/archive/<id>_<UTC>.owl      previous served files, newest ARCHIVE_KEEP kept

An update:
  1. Preconditions: a source URL, a worker that serves /data/<id>/<id>.owl,
     the served file visible here, no unresolved transaction
  2. HTTP version check (ETag / Last-Modified); unchanged ends the run
  3. Begin a transaction: a unique id, recorded on the registry entry
  4. Download to the transaction's staging file; identical content ends the run
  5. Validate on the worker with the ontology's import policy, exporting the
     merged ontology (imports merged in, no owl:imports) to the worker
  6. Index that export into the transaction's own ES index; check the
     document count against the validated class count
  7. Hot-swap the worker onto the staging file
  8. Archive the served file, promote the staging file in its place, and
     tell the worker its served path (finalizeUpdate)
  9. Move the ES alias to the new index; the previous index is kept for
     rollback, the one before it deleted
 10. Record hashes, status and history; end the transaction

A reindex indexes the served file into a new index and moves the alias.

Transactions. The record (registry field update_txn) is written before any
download and removed only when the run ends with a known state: success, or
a failure whose resources are all known to be unused and were cleaned up.
When the outcome is unknown (a worker task timed out or vanished, a request
got no answer, central was interrupted), the record stays, marked
recovery_required, and every later update, reindex, source change or batch
check of that ontology is refused until an operator reconciles the state
and resolves it (POST /admin/ontology/{id}/resolve_update). Nothing a
running task may still use is deleted. See deploy/README.md.

Transport. By default central and the worker share the ontology tree, as
above. A worker mapped in UPDATE_TRANSFER_CONFIG (see transfer.py) is on
another host: central downloads into its own ONTOLOGIES_DIR/.update-downloads/
and the transfer service on the worker's host stages the release at the same
/data/<id>/updates/<txn>/<id>.owl, promotes it (compare and set on the served
file's SHA-256) and removes the stage after finalize. Decisions about the
served file use what the service reports for the worker's host, never
central's copy, and the version markers are recorded only after the service
shows the served file holding the new release. Any outcome the service
cannot confirm leaves the transaction for recovery, as above.

Ownership. In queued mode the update runner (app/update_runner.py) passes a
fenced registry client: every registry write, and every _checkpoint before
a step with effects elsewhere (worker requests, index creation and
deletion, the alias, file promotion and removal), first checks that its job
still holds the global update slot, and raises OwnershipLost otherwise. A
runner whose job an operator released therefore stops before its next step.
A request already sent, or a worker task already started, is not undone.
"""

import asyncio
import glob
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import aiohttp

from app.es_manager import CentralESManager, ESUnavailable
from app.intake import transfer as update_transfer

logger = logging.getLogger(__name__)

# How long to wait for OntologyServer hot-swap to complete (seconds)
HOTSWAP_TIMEOUT = 1800
POLL_INTERVAL = 10
# Validation parses the staged file and, for an ontology with an import
# catalogue, its whole local closure; large ontologies need minutes.
VALIDATE_TIMEOUT = 900
# Previous served files kept in /data/<id>/archive/ after an update.
ARCHIVE_KEEP = max(1, int(os.getenv("UPDATE_ARCHIVE_KEEP", "3")))


def byte_limit(raw: Optional[str], default: int) -> Optional[int]:
    """A download size limit from the environment: default when unset, a
    whole number of bytes from 1 to 1 TiB, or None when invalid. An invalid
    limit refuses every download, and update_jobs.load_settings refuses
    every update request, rather than using another value."""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if 1 <= value <= 1 << 40 else None


# A download is bounded twice: the bytes received (the encoded size) and the
# bytes written once gzip is removed. Neither is ever held in memory whole.
MAX_DOWNLOAD_BYTES = byte_limit(os.getenv("UPDATE_MAX_DOWNLOAD_BYTES"), 4 << 30)
MAX_EXPANDED_BYTES = byte_limit(os.getenv("UPDATE_MAX_EXPANDED_BYTES"), 8 << 30)
# Bytes handed to the decoding thread at a time, and the most one
# decompression step may produce.
DOWNLOAD_CHUNK = 1 << 20
GZIP_MAGIC = b"\x1f\x8b"
# A .gz file sent with Content-Encoding: gzip has two layers.
MAX_GZIP_LAYERS = 2

BIOPORTAL_HOST = "data.bioontology.org"
# Same rule as the worker's import catalogues: the id becomes a path component.
_ONTOLOGY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ARCHIVE_NAME = re.compile(r"^.+_\d{8}T\d{6}\.owl$")

# One update or reindex per ontology at a time in this process. The
# persisted transaction record (update_txn) carries the guard across
# restarts and unknown outcomes.
_locks: Dict[str, asyncio.Lock] = {}
# One worker-side phase (validation, indexing, hot-swap) per worker at a
# time: each parses a whole ontology in the worker's heap or starts an
# indexer JVM, and a worker serves many ontologies.
_worker_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(ontology_id: str) -> asyncio.Lock:
    lock = _locks.get(ontology_id)
    if lock is None:
        lock = _locks[ontology_id] = asyncio.Lock()
    return lock


def _worker_lock(server_url: str) -> asyncio.Lock:
    key = server_url.rstrip("/")
    lock = _worker_locks.get(key)
    if lock is None:
        lock = _worker_locks[key] = asyncio.Lock()
    return lock


def update_in_progress(ontology_id: str) -> bool:
    lock = _locks.get(ontology_id)
    return bool(lock and lock.locked())


def txn_reason(ontology_id: str, entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why an unresolved transaction record blocks this ontology, or None.
    A record in state "running" that no run in this process holds was left
    by an interrupted central; it needs reconciling like any other."""
    txn = (entry or {}).get("update_txn")
    if not txn:
        return None
    return (f"transaction {txn.get('id')} ({txn.get('kind')}, phase {txn.get('phase')}) is "
            f"{txn.get('state')}: {txn.get('error') or 'interrupted'}; reconcile it and "
            f"POST /admin/ontology/{ontology_id}/resolve_update")


def blocked_reason(ontology_id: str, entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why an update, reindex or source change of this ontology must not
    start now, or None. Shared by every entry point."""
    if update_in_progress(ontology_id):
        return "an update or reindex of this ontology is running"
    return txn_reason(ontology_id, entry)


def new_txn_id() -> str:
    """Unique, lower case, valid as an ES index suffix and a directory name."""
    return datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S") + "-" + secrets.token_hex(3)


def ontology_paths(ontologies_dir: str, ontology_id: str, txn: Optional[str] = None,
                   remote: bool = False) -> Dict[str, Any]:
    """Where the pipeline reads and writes, as central and as the worker see it.
    With remote transport, central's download is its own, outside the
    ontology's directory; the transfer service puts the stage at
    worker_staging on the worker's host."""
    ont_dir = Path(ontologies_dir) / ontology_id
    paths = {
        "dir": ont_dir,
        "served": ont_dir / f"{ontology_id}.owl",
        "archive": ont_dir / "archive",
        "worker_served": f"/data/{ontology_id}/{ontology_id}.owl",
    }
    if txn:
        if remote:
            paths["txn_dir"] = Path(ontologies_dir) / ".update-downloads" / ontology_id / txn
        else:
            paths["txn_dir"] = ont_dir / "updates" / txn
        paths["staging"] = paths["txn_dir"] / f"{ontology_id}.owl"
        paths["worker_staging"] = f"/data/{ontology_id}/updates/{txn}/{ontology_id}.owl"
    return paths


async def worker_recovery_blocker(redis_client, server_url: str, own_txn: str) -> Optional[str]:
    """Check under the worker lock: a prior uncertain parse or task may still
    use this worker's heap even when it belongs to another ontology. Persisted
    records also cover an interrupted central process."""
    remote_phases = {"validate", "index", "hotswap", "restore"}
    for raw in await redis_client.hvals("registered_servers"):
        entry = json.loads(raw)
        record = entry.get("update_txn")
        if not isinstance(record, dict) or record.get("id") == own_txn:
            continue
        if str(record.get("worker", "")).rstrip("/") != server_url.rstrip("/"):
            continue
        if record.get("state") == "recovery_required" or record.get("phase") in remote_phases:
            return f"transaction {record.get('id')} of {entry.get('ontology_id') or entry.get('ontology')} needs reconciliation on this worker"
    return None


def source_headers(source_url: str) -> Optional[Dict[str, str]]:
    """Request headers for a source download. BioPortal download links need
    the operator's API key; the key is sent to that host only. Returns None
    when the key is needed but not configured."""
    if urlsplit(source_url).hostname == BIOPORTAL_HOST:
        key = os.getenv("BIOPORTAL_API_KEY", "")
        if not key:
            return None
        return {"Authorization": f"apikey token={key}"}
    return {}


def _secret(registry_entry: Dict[str, Any]) -> str:
    # Worker mutating servlets authenticate against the shared
    # ABEROWL_SECRET_KEY env, NOT the per-ontology registry secret_key (which
    # only guards registration). Prefer the shared secret.
    return os.getenv("ABEROWL_SECRET_KEY") or registry_entry.get("secret_key", "")


# ---------------------------------------------------------------------------
# Version checking
# ---------------------------------------------------------------------------

async def check_version(
    source_url: str,
    stored_etag: Optional[str],
    stored_last_modified: Optional[str],
    stored_md5: Optional[str],
    session: aiohttp.ClientSession,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Perform an HTTP HEAD to check whether the ontology has changed.

    Returns dict with keys:
      changed      – bool, True if an update is needed
      etag         – str or None
      last_modified – str or None
      need_md5     – bool, True if we had to do a full download for comparison
      permanent_redirect – str or None (new URL if 301/308)
      error        – str or None
      http_status  – int or None, the final HEAD status
    """
    result = {
        "changed": False,
        "etag": stored_etag,
        "last_modified": stored_last_modified,
        "need_md5": False,
        "permanent_redirect": None,
        "error": None,
        "http_status": None,
    }
    try:
        async with session.head(
            source_url,
            allow_redirects=True,
            headers=headers or {},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            result["http_status"] = resp.status
            # Track permanent redirects to update stored URL
            if resp.history:
                for r in resp.history:
                    if r.status in (301, 308):
                        result["permanent_redirect"] = str(resp.url)

            if resp.status in (404, 410):
                result["error"] = "source_gone"
                return result
            if resp.status in (405, 501):
                # HEAD not supported: decide by downloading and comparing MD5.
                result["need_md5"] = True
                result["changed"] = True
                return result
            if resp.status >= 500:
                result["error"] = f"server_error_{resp.status}"
                return result
            if not 200 <= resp.status < 300:
                # 401/403 (credentials), 429 (rate limit) and anything else
                # unexpected: never read as "unchanged".
                result["error"] = f"http_{resp.status}"
                return result

            new_etag = resp.headers.get("ETag")
            new_lm = resp.headers.get("Last-Modified")

            if new_etag:
                result["etag"] = new_etag
                result["changed"] = (new_etag != stored_etag)
            elif new_lm:
                result["last_modified"] = new_lm
                result["changed"] = (new_lm != stored_last_modified)
            else:
                # No version headers: need full download + MD5 comparison
                result["need_md5"] = True
                result["changed"] = True  # provisionally; caller checks MD5 after download

    except asyncio.TimeoutError:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class DownloadError(Exception):
    """A download that must not be used: over a limit, short, or corrupt."""


class _Gunzip:
    """Incremental gzip decoding. Each step produces at most DOWNLOAD_CHUNK
    bytes, so a highly compressed input never expands in memory at once.
    Several members (concatenated gzip files) are read in turn, as gzip
    itself does; after the last one only zero padding may follow."""

    def __init__(self):
        self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._padding = False

    def feed(self, data: bytes):
        while True:
            if self._padding:
                if data.strip(b"\0"):
                    raise DownloadError("data follows the gzip stream")
                return
            if self._d.eof:
                if not data:
                    return
                if data[0] == 0:
                    self._padding = True
                    continue
                self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                out = self._d.decompress(data, DOWNLOAD_CHUNK)
            except zlib.error as e:
                raise DownloadError(f"corrupt gzip data: {e}") from None
            if out:
                yield out
            data = self._d.unused_data if self._d.eof else self._d.unconsumed_tail
            if not self._d.eof and not data and len(out) < DOWNLOAD_CHUNK:
                return  # needs more input

    def close(self) -> None:
        if not self._d.eof and not self._padding:
            raise DownloadError("truncated gzip stream")


class _Decoder:
    """Removes gzip layers, each recognised by its magic bytes rather than
    by headers or the URL, up to MAX_GZIP_LAYERS. Anything else passes
    through unchanged, as the previous download did with a body that was not
    gzip after all."""

    def __init__(self, depth: int = 0):
        self._depth = depth
        self._head = b""
        self._decided = False
        self._gz: Optional[_Gunzip] = None
        self._inner: Optional["_Decoder"] = None

    def feed(self, data: bytes):
        if not self._decided:
            self._head += data
            if len(self._head) < len(GZIP_MAGIC):
                return
            data, self._head, self._decided = self._head, b"", True
            if data.startswith(GZIP_MAGIC) and self._depth < MAX_GZIP_LAYERS:
                self._gz, self._inner = _Gunzip(), _Decoder(self._depth + 1)
        if self._gz is None:
            yield data
            return
        for out in self._gz.feed(data):
            yield from self._inner.feed(out)

    def close(self):
        if self._head:
            yield self._head
            self._head = b""
        if self._gz is not None:
            self._gz.close()
            yield from self._inner.close()


class _DownloadFile:
    """Decodes, hashes and writes a download in the order it arrives, into
    a partial file this run created (exclusively, so never another's). Its
    methods run in a worker thread, one at a time."""

    def __init__(self, path: Path):
        self.path = path
        self._file = open(path, "xb")
        self._decoder = _Decoder()
        self.md5 = hashlib.md5()
        self.size = 0

    def _write(self, out: bytes) -> None:
        self.size += len(out)
        if self.size > MAX_EXPANDED_BYTES:
            raise DownloadError(f"more than {MAX_EXPANDED_BYTES} bytes once decompressed")
        self._file.write(out)
        self.md5.update(out)

    def feed(self, data: bytes) -> None:
        for out in self._decoder.feed(data):
            self._write(out)

    def finish(self) -> None:
        for out in self._decoder.close():
            self._write(out)
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()

    def discard(self) -> None:
        self._file.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def _content_length(value: Optional[str]) -> Optional[int]:
    try:
        n = int(value) if value is not None else None
    except ValueError:
        return None
    return n if n is not None and n >= 0 else None


async def download_ontology(
    source_url: str,
    dest_path: str,
    session: aiohttp.ClientSession,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Stream-download source_url to dest_path.

    The body is read in chunks and never held whole. Gzip, whether sent as
    Content-Encoding or as the file itself (.owl.gz), is removed here
    incrementally; the request accepts only gzip or identity, and the
    session must be created with auto_decompress=False so the client does
    not decode it first. Decoding, hashing and writing run in a worker
    thread. Over MAX_DOWNLOAD_BYTES received or MAX_EXPANDED_BYTES written,
    a body that does not match its Content-Length, another encoding, and a
    truncated or corrupt gzip stream are errors. The file appears at
    dest_path, by rename, only when complete; a failed download removes its
    own partial file and nothing else.

    Returns {"md5", "size", "received"} on success, or {"error": ...}.
    """
    logger.info("Downloading %s → %s", source_url, dest_path)
    if MAX_DOWNLOAD_BYTES is None or MAX_EXPANDED_BYTES is None:
        return {"error": "UPDATE_MAX_DOWNLOAD_BYTES or UPDATE_MAX_EXPANDED_BYTES is not a whole number "
                         "of bytes from 1 to 1 TiB"}
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    request_headers = {**(headers or {}), "Accept-Encoding": "gzip"}
    target: Optional[_DownloadFile] = None
    try:
        async with session.get(
            source_url,
            allow_redirects=True,
            headers=request_headers,
            timeout=aiohttp.ClientTimeout(total=3600),
        ) as resp:
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}"}
            encoding = (resp.headers.get("Content-Encoding") or "identity").strip().lower()
            if encoding not in ("identity", "gzip", "x-gzip"):
                return {"error": f"unsupported Content-Encoding: {encoding}"}
            declared = _content_length(resp.headers.get("Content-Length"))
            if declared is not None and declared > MAX_DOWNLOAD_BYTES:
                return {"error": f"Content-Length {declared} is over the limit of {MAX_DOWNLOAD_BYTES} bytes"}

            target = _DownloadFile(dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.part"))
            received = 0
            pending = bytearray()
            async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK):
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise DownloadError(f"more than {MAX_DOWNLOAD_BYTES} bytes received")
                if declared is not None and received > declared:
                    raise DownloadError(f"more than the {declared} bytes of its Content-Length")
                pending += chunk
                if len(pending) >= DOWNLOAD_CHUNK:
                    data = bytes(pending)
                    pending.clear()
                    await asyncio.to_thread(target.feed, data)
            if pending:
                await asyncio.to_thread(target.feed, bytes(pending))
            if declared is not None and received != declared:
                raise DownloadError(f"truncated: {received} of {declared} bytes")
            await asyncio.to_thread(target.finish)
        os.replace(target.path, dest)
        md5_hex = target.md5.hexdigest()
        logger.info("Downloaded %s bytes (%s received, md5=%s) to %s", target.size, received, md5_hex, dest_path)
        return {"md5": md5_hex, "size": target.size, "received": received}

    except Exception as e:
        # A cancellation is not caught here: the transaction keeps its
        # directory, partial file included, for recovery.
        logger.error("Download error for %s: %s", source_url, e)
        if target is not None:
            await asyncio.to_thread(target.discard)
        return {"error": str(e) or type(e).__name__}


def file_md5(path: str) -> Optional[str]:
    """MD5 of a file, or None if it cannot be read."""
    md5 = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                md5.update(chunk)
    except OSError:
        return None
    return md5.hexdigest()


# ---------------------------------------------------------------------------
# OntologyServer API helpers
# ---------------------------------------------------------------------------

def _api(server_url: str, script: str) -> str:
    return f"{server_url.rstrip('/')}/api/{script}"


async def worker_served_path(server_url: str, ontology_id: str) -> Dict[str, Any]:
    """The file the worker reports it serves the ontology from
    (listLoadedOntologies.groovy). Returns {"state": "ok", "path", "status"},
    {"state": "not_loaded"} or {"state": "unreachable", "message"}."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _api(server_url, "listLoadedOntologies.groovy"),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200 or not isinstance(data, dict):
                    return {"state": "unreachable", "message": f"HTTP {resp.status}"}
    except Exception as e:
        return {"state": "unreachable", "message": str(e) or type(e).__name__}
    for o in data.get("ontologies") or []:
        if isinstance(o, dict) and o.get("ontologyId") == ontology_id:
            return {"state": "ok", "path": o.get("path") or "", "status": o.get("status")}
    return {"state": "not_loaded"}


async def validate_staged(
    server_url: str, owl_path_on_server: str, ontology_id: Optional[str] = None,
    secret_key: str = "", served_path: Optional[str] = None, export_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask the worker to parse a staged file the way a hot-swap of
    ontology_id would: with its import catalogue if it has one, otherwise
    with imports suppressed. With export_id it also writes the merged
    ontology for indexing and reports its exportPath.

    Returns {"ok": bool, "answered": bool, "message": str, ...worker summary}.
    answered is False when the worker gave no HTTP answer."""
    payload = {"secretKey": secret_key, "owlPath": owl_path_on_server}
    if ontology_id:
        payload["ontologyId"] = ontology_id
    if served_path:
        payload["servedPath"] = served_path
    if export_id:
        payload["exportId"] = export_id
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _api(server_url, "validateOntology.groovy"),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                if resp.status == 200 and data.get("status") == "ok":
                    return {**data, "ok": True, "answered": True, "message": ""}
                message = data.get("message") or f"HTTP {resp.status}"
                logger.warning("Validation of %s failed: %s", owl_path_on_server, message)
                return {"ok": False, "answered": True, "message": message}
    except Exception as e:
        logger.error("Validation error for %s: %s", owl_path_on_server, e)
        return {"ok": False, "answered": False, "message": str(e) or type(e).__name__}


async def validate_via_server(server_url: str, owl_path_on_server: str) -> bool:
    """Ask the OntologyServer to validate an OWL file using OWLAPI."""
    secret = os.getenv("ABEROWL_SECRET_KEY", "")
    return (await validate_staged(server_url, owl_path_on_server, secret_key=secret))["ok"]


async def start_task(server_url: str, script: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to a worker servlet that starts a background task.

    Returns {"state": "started", "task_id"}, {"state": "refused", "message"}
    when the worker answered that it did not start one (any 4xx, or the
    connection was never made), or {"state": "unknown", "message"} when it
    may have started one (no answer, a 5xx, or an unreadable answer)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _api(server_url, script), json=payload, timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status in (200, 202) and isinstance(data, dict) and data.get("taskId"):
                    return {"state": "started", "task_id": data["taskId"]}
                message = (data or {}).get("message") if isinstance(data, dict) else None
                message = f"HTTP {resp.status}: {message or 'no task id'}"
                if 400 <= resp.status < 500:
                    return {"state": "refused", "message": message}
                return {"state": "unknown", "message": message}
    except aiohttp.ClientConnectorError as e:
        return {"state": "refused", "message": f"cannot connect: {e}"}
    except Exception as e:
        return {"state": "unknown", "message": f"no answer: {str(e) or type(e).__name__}"}


async def start_hotswap(server_url, secret_key, ontology_id, owl_path_on_server,
                        served_path=None, reasoner_type=None) -> Dict[str, Any]:
    payload = {"secretKey": secret_key, "ontologyId": ontology_id, "owlPath": owl_path_on_server}
    if served_path:
        payload["servedPath"] = served_path
    if reasoner_type:
        payload["reasonerType"] = reasoner_type
    return await start_task(server_url, "updateOntology.groovy", payload)


async def start_indexing(server_url, secret_key, ontology_id, owl_path_on_server, class_index,
                         ontology_name, description, ontology_index="aberowl_ontologies") -> Dict[str, Any]:
    # The worker servlet reads camelCase params and gets the ES url from its own env.
    payload = {
        "secretKey": secret_key,
        "ontologyId": ontology_id,
        "owlPath": owl_path_on_server,
        "classIndexName": class_index,
        "ontologyIndexName": ontology_index,
        "name": ontology_name,
        "description": description,
        "freshIndex": "false",
    }
    return await start_task(server_url, "triggerIndexing.groovy", payload)


async def trigger_hotswap(
    server_url: str,
    secret_key: str,
    ontology_id: str,
    owl_path_on_server: str,
    callback_url: Optional[str] = None,
    reasoner_type: Optional[str] = None,
) -> Optional[str]:
    """POST to the OntologyServer to trigger an async hot-swap. Returns the
    task_id, or None when none was started or the outcome is unknown."""
    payload = {"secretKey": secret_key, "ontologyId": ontology_id, "owlPath": owl_path_on_server}
    if callback_url:
        payload["callbackUrl"] = callback_url
    if reasoner_type:
        payload["reasonerType"] = reasoner_type
    started = await start_task(server_url, "updateOntology.groovy", payload)
    return started.get("task_id")


async def trigger_indexing(
    server_url: str,
    secret_key: str,
    ontology_id: str,
    owl_path_on_server: str,
    es_url: str,
    ontology_index: str,
    class_index: str,
    ontology_name: str,
    description: str,
    callback_url: Optional[str] = None,
) -> Optional[str]:
    """POST to the OntologyServer to trigger async ES indexing. Returns
    task_id, or None when none was started or the outcome is unknown."""
    started = await start_indexing(server_url, secret_key, ontology_id, owl_path_on_server,
                                   class_index, ontology_name, description, ontology_index)
    return started.get("task_id")


async def poll_task(
    server_url: str, task_id: str, timeout_secs: int = HOTSWAP_TIMEOUT
) -> Dict[str, Any]:
    """Poll the worker's updateStatus for an indexing or hot-swap task.

    Returns {"status", "message", "data"} with status one of "success",
    "failed", "lost" (the worker does not know the task: it restarted, or
    the id is not its own; the outcome is unknown) or "timeout" (outcome
    unknown). data is the worker's last task record.
    """
    url = _api(server_url, "updateStatus.groovy")
    elapsed = 0
    async with aiohttp.ClientSession() as session:
        while elapsed < timeout_secs:
            try:
                async with session.get(
                    url,
                    params={"taskId": task_id},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 404:
                        logger.error("Task %s is unknown to the worker", task_id)
                        return {"status": "lost", "message": f"task {task_id} unknown to the worker", "data": {}}
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        status = data.get("status")
                        if status in ("success", "failed"):
                            log = logger.info if status == "success" else logger.error
                            log("Task %s %s: %s", task_id, status, data.get("message"))
                            return {"status": status, "message": data.get("message") or "", "data": data}
                        # "pending" or "running" — keep polling
            except Exception as e:
                logger.warning("Poll error for task %s: %s", task_id, e)

            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

    logger.error("Task %s timed out after %ss", task_id, timeout_secs)
    return {"status": "timeout", "message": f"no result after {timeout_secs}s", "data": {}}


async def task_status_once(server_url: Optional[str], task_id: Optional[str]) -> str:
    """One look at a task: "pending", "success", "failed", "unknown_task"
    (404) or "unreachable"."""
    if not server_url or not task_id:
        return "unreachable"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _api(server_url, "updateStatus.groovy"), params={"taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 404:
                    return "unknown_task"
                if resp.status != 200:
                    return "unreachable"
                data = await resp.json(content_type=None)
                status = (data or {}).get("status")
                return status if status in ("success", "failed") else "pending"
    except Exception:
        return "unreachable"


async def wait_for_hotswap(
    server_url: str, task_id: str, timeout_secs: int = HOTSWAP_TIMEOUT
) -> bool:
    """Poll the OntologyServer until the hot-swap succeeds, fails, or times out."""
    return (await poll_task(server_url, task_id, timeout_secs))["status"] == "success"


async def wait_for_task(
    server_url: str, task_id: str, timeout_secs: int = HOTSWAP_TIMEOUT
) -> bool:
    """Poll updateStatus for any generic task (indexing, hotswap)."""
    return await wait_for_hotswap(server_url, task_id, timeout_secs)


async def _finalize_call(server_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _api(server_url, "finalizeUpdate.groovy"), json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                data = data if isinstance(data, dict) else {}
                return {"ok": resp.status == 200 and data.get("status") == "ok",
                        "message": data.get("message") or f"HTTP {resp.status}", **data}
    except Exception as e:
        return {"ok": False, "message": f"no answer: {str(e) or type(e).__name__}"}


async def finalize_served_path(server_url, secret_key, ontology_id, staged_path, served_path) -> Dict[str, Any]:
    """Tell the worker that the file it hot-swapped from (staged_path) is now
    served_path. The worker only replaces staged_path (compare and set)."""
    return await _finalize_call(server_url, {
        "secretKey": secret_key, "ontologyId": ontology_id, "action": "finalize",
        "owlPath": staged_path, "servedPath": served_path,
    })


async def discard_export(server_url, secret_key, ontology_id, export_id) -> bool:
    """Ask the worker to delete a validation export no task will index."""
    result = await _finalize_call(server_url, {
        "secretKey": secret_key, "ontologyId": ontology_id, "action": "discardExport",
        "exportId": export_id,
    })
    if not result["ok"]:
        logger.warning("%s: export %s not discarded: %s", ontology_id, export_id, result["message"])
    return result["ok"]


# ---------------------------------------------------------------------------
# Import catalogue dependents
# ---------------------------------------------------------------------------

def find_catalogue_dependents(ontologies_dir: str, worker_path: str) -> List[str]:
    """Ontologies whose import catalogue (/data/import_catalogues/<id>.json)
    lists worker_path. Their serving copy merged the file as it was at their
    last load, and they do not reload on their own when it is replaced.
    Paths are compared as written, after normalisation; a catalogue that
    reaches the file through a symlink is not detected."""
    target = os.path.normpath(worker_path)
    dependents = []
    for path in sorted(glob.glob(os.path.join(ontologies_dir, "import_catalogues", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                imports = json.load(f).get("imports") or {}
        except (OSError, ValueError, AttributeError) as e:
            logger.warning("Cannot read import catalogue %s: %s", path, e)
            continue
        if not isinstance(imports, dict):
            continue
        if any(isinstance(v, str) and os.path.normpath(v) == target for v in imports.values()):
            dependents.append(Path(path).stem)
    return dependents


# ---------------------------------------------------------------------------
# Outcomes and registry records
# ---------------------------------------------------------------------------

def _outcome(status: str, error: Optional[str] = None, message: str = "", **extra) -> Dict[str, Any]:
    """Pipeline result. status is "updated", "unchanged", "skipped" or
    "failed"; "changed" is kept for callers that predate "status".
    recovery_required is True when the run left an unresolved transaction."""
    result = {
        "success": status in ("updated", "unchanged"),
        "status": status,
        "changed": {"updated": True, "unchanged": False}.get(status),
        "error": error,
        "message": message,
        "recovery_required": False,
    }
    result.update(extra)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_entry(redis_client, ontology_id: str) -> Dict[str, Any]:
    raw = await redis_client.hget("registered_servers", ontology_id)
    return json.loads(raw) if raw else {"ontology_id": ontology_id}


async def _save_entry(redis_client, ontology_id: str, entry: Dict[str, Any]) -> None:
    await redis_client.hset("registered_servers", ontology_id, json.dumps(entry))


def _set_update_status(entry: Dict[str, Any], status: str, error: Optional[str]) -> None:
    # A paused ontology stays paused; a manual run's outcome is still
    # recorded in last_check_status.
    if entry.get("update_status") != "disabled":
        entry["update_status"] = status
    entry["update_error"] = error


def _append_history(entry: Dict[str, Any], item: Dict[str, Any]) -> None:
    history = entry.get("update_history", [])
    history.append(item)
    entry["update_history"] = history[-10:]


async def _update_registry_status(
    redis_client, ontology_id: str, update_status: str, error_msg: str, end_txn: Optional[str] = None
) -> None:
    """Record a failure whose state is known. end_txn removes that
    transaction record in the same write."""
    entry = await _load_entry(redis_client, ontology_id)
    now = _now()
    _set_update_status(entry, update_status, error_msg)
    entry["last_checked"] = now
    entry["last_check_status"] = "failed"
    entry["last_check_error"] = f"{update_status}: {error_msg}"
    item = {"timestamp": now, "status": update_status, "error": error_msg}
    if end_txn and (entry.get("update_txn") or {}).get("id") == end_txn:
        entry.pop("update_txn")
        item["txn"] = end_txn
    _append_history(entry, item)
    await _save_entry(redis_client, ontology_id, entry)


async def _fail(redis_client, ontology_id, update_status, error, message, txn=None) -> Dict[str, Any]:
    await _update_registry_status(redis_client, ontology_id, update_status, message, end_txn=txn)
    logger.warning("%s: update failed: %s (%s)", ontology_id, error, message)
    return _outcome("failed", error, message)


async def _record_check(redis_client, ontology_id, check_status, error, message) -> None:
    """Record a check that did not reach the source (a skip). update_status,
    which describes the last real attempt, is left alone."""
    raw = await redis_client.hget("registered_servers", ontology_id)
    if not raw:
        return
    entry = json.loads(raw)
    entry["last_checked"] = _now()
    entry["last_check_status"] = check_status
    entry["last_check_error"] = f"{error}: {message}"
    await _save_entry(redis_client, ontology_id, entry)


async def _record_unchanged(redis_client, ontology_id, version_info, md5, txn=None, warnings=None) -> None:
    """The served release is the upstream one: store the version markers so
    the next check is a HEAD request only."""
    entry = await _load_entry(redis_client, ontology_id)
    entry.update({
        "source_etag": version_info.get("etag"),
        "source_last_modified": version_info.get("last_modified"),
        "source_md5": md5,
        "last_checked": _now(),
        "last_check_status": "unchanged",
        "last_check_error": None,
    })
    if version_info.get("permanent_redirect"):
        entry["source_resolved_url"] = version_info["permanent_redirect"]
    if txn and (entry.get("update_txn") or {}).get("id") == txn:
        entry.pop("update_txn")
    if warnings:
        entry["update_warnings"] = warnings
    else:
        entry.pop("update_warnings", None)
    _set_update_status(entry, "ok", None)
    await _save_entry(redis_client, ontology_id, entry)


async def _set_fields(redis_client, ontology_id, **fields) -> None:
    """Set (or, with None, remove) fields of the registry entry as it is now."""
    entry = await _load_entry(redis_client, ontology_id)
    for key, value in fields.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    await _save_entry(redis_client, ontology_id, entry)


def _unfinished_since_success(entry: Dict[str, Any]) -> bool:
    """Whether a run stopped for recovery after the last complete update.
    Such a run may have replaced the served file without moving the search
    alias or recording dependents, so the served file then says nothing
    about what the rest of the system holds."""
    for item in reversed(entry.get("update_history") or []):
        status = item.get("status")
        if status == "success":
            return False
        if status in ("recovery_required", "resolved_by_operator"):
            return True
    return False


async def _txn_begin(redis_client, ontology_id, record: Dict[str, Any]) -> None:
    entry = await _load_entry(redis_client, ontology_id)
    entry["update_txn"] = {**record, "state": "running", "started": _now(), "updated": _now()}
    await _save_entry(redis_client, ontology_id, entry)


async def _checkpoint(redis_client) -> None:
    """Before a step with effects outside the registry: a worker request, an
    index created or deleted, the alias moved, a file replaced or removed.
    The update runner's client (update_jobs.FencedRedis) raises
    OwnershipLost here once its job no longer holds the global update slot,
    so a runner whose claim was released stops before the next step. Its
    registry writes are fenced the same way, atomically. Other clients
    (inline mode, tests) have no fence."""
    check = getattr(redis_client, "check_claim", None)
    if check is not None:
        await check()


async def _txn_set(redis_client, ontology_id, txn: str, **fields) -> None:
    """Update the transaction record, e.g. its phase or the worker task it
    waits for. Every phase starts here, so this is also a checkpoint: a
    record that is gone (resolved meanwhile) writes nothing, and must not let
    a run whose ownership ended go on."""
    await _checkpoint(redis_client)
    entry = await _load_entry(redis_client, ontology_id)
    record = entry.get("update_txn")
    if not record or record.get("id") != txn:
        return
    record.update(fields, updated=_now())
    await _save_entry(redis_client, ontology_id, entry)


async def _recovery(redis_client, ontology_id, txn: str, error: str, message: str) -> Dict[str, Any]:
    """The run stops with an unknown or inconsistent state. Its resources are
    kept, and the record blocks further runs until an operator resolves it."""
    entry = await _load_entry(redis_client, ontology_id)
    record = entry.get("update_txn") or {"id": txn}
    record.update(state="recovery_required", error=f"{error}: {message}", updated=_now())
    entry["update_txn"] = record
    now = _now()
    _set_update_status(entry, "recovery_required", f"{error}: {message}")
    entry["last_checked"] = now
    entry["last_check_status"] = "recovery_required"
    entry["last_check_error"] = f"{error}: {message}"
    _append_history(entry, {"timestamp": now, "status": "recovery_required", "error": f"{error}: {message}",
                            "txn": txn, "phase": record.get("phase")})
    await _save_entry(redis_client, ontology_id, entry)
    logger.error("%s: transaction %s needs recovery: %s (%s)", ontology_id, txn, error, message)
    return _outcome("failed", error, message, recovery_required=True, txn=txn)


async def _guarded(redis_client, ontology_id, txn, run):
    """Run a transaction body; an unexpected exception or a cancellation
    leaves the transaction for recovery rather than cleaning anything up."""
    try:
        return await run()
    except asyncio.CancelledError:
        try:
            await _recovery(redis_client, ontology_id, txn, "cancelled", "the run was cancelled")
        except Exception:
            logger.exception("%s: could not record the cancelled transaction %s", ontology_id, txn)
        raise
    except ESUnavailable as e:
        return await _recovery(redis_client, ontology_id, txn, "es_unavailable", str(e))
    except Exception as e:
        logger.exception("%s: transaction %s raised", ontology_id, txn)
        return await _recovery(redis_client, ontology_id, txn, "unexpected", f"{type(e).__name__}: {e}")


def resolve_entry(entry: Dict[str, Any], txn: str, note: str, task_state: Optional[str]) -> bool:
    """Remove the transaction record txn from entry (in place) after an
    operator reconciled its state. False when the record is another one."""
    record = entry.get("update_txn")
    if not record or record.get("id") != txn:
        return False
    entry.pop("update_txn")
    now = _now()
    _set_update_status(entry, "resolved_by_operator", note or None)
    entry["last_check_status"] = "resolved_by_operator"
    entry["last_check_error"] = None
    _append_history(entry, {"timestamp": now, "status": "resolved_by_operator", "txn": txn,
                            "phase": record.get("phase"), "task_state": task_state, "note": note})
    return True


async def resolve_transaction(redis_client, ontology_id: str, txn: str, note: str, task_state: Optional[str]) -> bool:
    """Remove an unresolved transaction record after an operator reconciled
    its state. Deletes nothing else."""
    entry = await _load_entry(redis_client, ontology_id)
    if not resolve_entry(entry, txn, note, task_state):
        return False
    await _save_entry(redis_client, ontology_id, entry)
    return True


def _remove_txn_dir(paths: Dict[str, Any]) -> None:
    """Delete the transaction's staging directory, once nothing uses it."""
    txn_dir = paths.get("txn_dir")
    if not txn_dir:
        return
    try:
        shutil.rmtree(txn_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Could not remove staging directory %s: %s", txn_dir, e)
    try:
        txn_dir.parent.rmdir()  # updates/, only when empty
    except OSError:
        pass


async def _delete_unless_alias_target(es_mgr, ontology_id: str, index: Optional[str]) -> bool:
    """Delete an index the alias does not point to. Raises ESUnavailable when
    that cannot be checked."""
    if not index:
        return False
    if await es_mgr.get_alias_target(ontology_id) == index:
        logger.error("%s: refusing to delete %s, the alias target", ontology_id, index)
        return False
    return await es_mgr.delete_index(index)


async def _move_alias(es_mgr, ontology_id: str, new: str, old: Optional[str]) -> str:
    """Point the alias at new. Returns "moved", "unchanged" (it still points
    at old, so new is unused) or raises ESUnavailable when the state cannot
    be read back."""
    await es_mgr.swap_alias(ontology_id, new, old)
    target = await es_mgr.get_alias_target(ontology_id)
    if target == new:
        return "moved"
    if target == old:
        return "unchanged"
    raise ESUnavailable(f"alias points at {target}, neither {new} nor {old}")


async def _retire_previous(es_mgr, ontology_id: str, entry: Dict[str, Any], keep: List[Optional[str]]) -> None:
    """Delete the index that was kept for rollback by the run before this
    one. The index the alias pointed to until now becomes the kept one."""
    previous = entry.get("previous_es_index")
    if previous and previous not in keep:
        await _delete_unless_alias_target(es_mgr, ontology_id, previous)


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------

async def execute_reindex(
    ontology_id: str,
    registry_entry: Dict[str, Any],
    redis_client,
    es_mgr,
    ontologies_base_path: str,
    es_url: str,
) -> Dict[str, Any]:
    """Re-index an ontology into ES from the OWL the worker ALREADY serves —
    no download or hot-swap. Validate and export the current source using
    the worker's import policy, then index that complete, import-free
    export. Move the alias only after its document count matches the
    validated class count; retain the previous index for rollback."""
    lock = _lock_for(ontology_id)
    if lock.locked():
        return _outcome("skipped", "update_in_progress", "another update or reindex of this ontology is running")
    async with lock:
        entry = await _load_entry(redis_client, ontology_id)
        reason = txn_reason(ontology_id, entry)
        if reason:
            return _outcome("skipped", "recovery_required", reason)
        server_url = entry.get("server_url") or entry.get("url")
        if not server_url:
            await _update_registry_status(redis_client, ontology_id, "reindex_failed", "no server url")
            return _outcome("failed", "no_server_url", "no worker is registered for this ontology")
        try:
            old_index = await es_mgr.get_alias_target(ontology_id)
        except ESUnavailable as e:
            await _update_registry_status(redis_client, ontology_id, "reindex_failed", str(e))
            return _outcome("failed", "es_unavailable", str(e))
        txn = new_txn_id()
        new_index = CentralESManager.transaction_index_name(ontology_id, txn)
        await _txn_begin(redis_client, ontology_id, {
            "id": txn, "kind": "reindex", "phase": "queued", "worker": server_url, "index": new_index})
        return await _guarded(redis_client, ontology_id, txn, lambda: _reindex(
            ontology_id, entry, redis_client, es_mgr, server_url, txn, new_index, old_index))


async def _reindex(ontology_id, entry, redis_client, es_mgr, server_url, txn, new_index, old_index) -> Dict[str, Any]:
    secret_key = _secret(entry)

    async def fail(error, message):
        await _update_registry_status(redis_client, ontology_id, "reindex_failed", message, end_txn=txn)
        return _outcome("failed", error, message)

    async with _worker_lock(server_url):
        blocked = await worker_recovery_blocker(redis_client, server_url, txn)
        if blocked:
            return await fail("worker_recovery_required", blocked)
        await _txn_set(redis_client, ontology_id, txn, phase="validate")
        served_path = f"/data/{ontology_id}/{ontology_id}.owl"
        validation = await validate_staged(
            server_url, served_path, ontology_id, secret_key,
            served_path, export_id=txn)
        await _checkpoint(redis_client)
        if not validation["ok"]:
            if not validation["answered"]:
                return await _recovery(redis_client, ontology_id, txn,
                                       "validation_unknown", validation["message"])
            return await fail("reindex_validation_failed", validation["message"])
        export_path = validation.get("exportPath")
        class_count = validation.get("classCount")
        if not export_path or not isinstance(class_count, int):
            await discard_export(server_url, secret_key, ontology_id, txn)
            return await fail("worker_outdated", "the worker returned no validated class export")
        await _txn_set(redis_client, ontology_id, txn, phase="index", export=export_path)
        if not await es_mgr.create_class_index(new_index):
            await _checkpoint(redis_client)
            await discard_export(server_url, secret_key, ontology_id, txn)
            return await fail("es_index_create_failed", f"could not create {new_index}")
        await _checkpoint(redis_client)
        started = await start_indexing(
            server_url, secret_key, ontology_id, export_path, new_index,
            entry.get("name", ontology_id), entry.get("description", ""))
        await _checkpoint(redis_client)
        if started["state"] == "refused":
            await discard_export(server_url, secret_key, ontology_id, txn)
            await es_mgr.delete_index(new_index)
            return await fail("reindex_trigger_failed", started["message"])
        if started["state"] != "started":
            return await _recovery(redis_client, ontology_id, txn, "indexing_unknown", started["message"])
        await _txn_set(redis_client, ontology_id, txn, task_id=started["task_id"], task_kind="index")
        indexing = await poll_task(server_url, started["task_id"])
        # A poll can last until HOTSWAP_TIMEOUT: check ownership before acting on it.
        await _checkpoint(redis_client)
        if indexing["status"] in ("success", "failed"):
            await _txn_set(redis_client, ontology_id, txn, phase="index_finished", task_id=None)

    if indexing["status"] == "failed":
        await es_mgr.delete_index(new_index)
        return await fail("reindex_failed", f"ES indexing failed: {indexing['message']}")
    if indexing["status"] != "success":
        return await _recovery(redis_client, ontology_id, txn, "indexing_unknown",
                               f"ES indexing {indexing['status']}: {indexing['message']}")

    count = await es_mgr.count_documents(new_index)
    if count == 0:
        await _checkpoint(redis_client)
        await es_mgr.delete_index(new_index)
        return await fail("index_empty", f"{new_index} holds no documents")

    if count != class_count:
        await _checkpoint(redis_client)
        await es_mgr.delete_index(new_index)
        return await fail("index_count_mismatch",
                          f"index has {count} documents; validation reported {class_count} classes")

    await _txn_set(redis_client, ontology_id, txn, phase="alias", task_id=None)
    if await _move_alias(es_mgr, ontology_id, new_index, old_index) == "unchanged":
        await _checkpoint(redis_client)
        await es_mgr.delete_index(new_index)
        return await fail("alias_swap_failed", f"search still uses {old_index}; {new_index} was deleted")

    await _checkpoint(redis_client)
    current = await _load_entry(redis_client, ontology_id)
    await _retire_previous(es_mgr, ontology_id, current, [new_index, old_index])
    current = await _load_entry(redis_client, ontology_id)
    current.update({
        "active_es_index": new_index,
        "previous_es_index": old_index,
        "update_status": "ok" if current.get("update_status") != "disabled" else "disabled",
        "update_error": None,
        "last_indexed": _now(),
    })
    current.pop("update_txn", None)
    _append_history(current, {"timestamp": _now(), "status": "reindexed", "txn": txn,
                              "es_index": new_index, "documents": count})
    await _save_entry(redis_client, ontology_id, current)
    logger.info("Reindex complete for %s -> %s (%d documents)", ontology_id, new_index, count)
    return _outcome("updated", es_index=new_index, documents=count)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def execute_update_pipeline(
    ontology_id: str,
    registry_entry: Dict[str, Any],
    redis_client,
    es_mgr,
    ontologies_base_path: str,
    es_url: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Execute the full update pipeline for one ontology.

    ontologies_base_path is the ontology tree as THIS process sees it (the
    worker sees the same tree at /data). force skips the unchanged checks, so
    the current upstream release is downloaded, validated and swapped in even
    when it matches the served one. registry_entry is only a hint: the
    registry is read again under the lock.

    Returns {"success", "status", "changed", "error", "message",
    "recovery_required", ...}; see _outcome. Every outcome, including a skip,
    is recorded on the registry entry.
    """
    lock = _lock_for(ontology_id)
    if lock.locked():
        return _outcome("skipped", "update_in_progress", "another update or reindex of this ontology is running")
    async with lock:
        return await _update(ontology_id, redis_client, es_mgr, ontologies_base_path, force)


async def _update(ontology_id, redis_client, es_mgr, ontologies_dir, force):
    entry = await _load_entry(redis_client, ontology_id)

    async def skip(error: str, message: str) -> Dict[str, Any]:
        await _record_check(redis_client, ontology_id, "skipped", error, message)
        logger.warning("%s: update skipped: %s (%s)", ontology_id, error, message)
        return _outcome("skipped", error, message)

    if not _ONTOLOGY_ID.match(ontology_id or ""):
        return _outcome("skipped", "invalid_ontology_id", "the id cannot be used as a directory name")
    if entry.get("update_txn"):
        return await skip("recovery_required", txn_reason(ontology_id, entry))
    source_url = entry.get("source_url")
    # Registry entries store the worker URL under "url"; older code/paths used
    # "server_url".
    server_url = entry.get("server_url") or entry.get("url")
    if not source_url:
        return await skip("no_source_url", "no source_url is configured for this ontology")
    if not server_url:
        return await skip("no_server_url", "no worker is registered for this ontology")
    try:
        target = update_transfer.target_for(server_url)
    except update_transfer.TransferConfigError as e:
        # Never fall back to the shared directory when the mapping is broken.
        return await skip("transfer_config_invalid", str(e))
    remote = update_transfer.RemoteTransfer(target, ontology_id) if target else None
    paths = ontology_paths(ontologies_dir, ontology_id)
    if remote is None and not paths["served"].is_file():
        # Either the ontology is not stored as /data/<id>/<id>.owl, or this
        # process does not see the ontology tree (ONTOLOGIES_DIR).
        return await skip("served_file_missing", f"{paths['served']} does not exist on the central server")
    headers = source_headers(source_url)
    if headers is None:
        return await _fail(redis_client, ontology_id, "check_failed", "bioportal_api_key_missing",
                           "the source is a BioPortal download and BIOPORTAL_API_KEY is not set")

    # The worker must serve the ontology from the file this pipeline replaces;
    # otherwise a restart would load something else, and a staging path left
    # by an unfinished update means its state needs reconciling first.
    served = await worker_served_path(server_url, ontology_id)
    if served["state"] == "unreachable":
        return await skip("worker_unreachable", served["message"])
    if served["state"] == "not_loaded":
        return await skip("worker_not_serving", f"the worker does not report {ontology_id} as loaded")
    if served["path"] != paths["worker_served"]:
        return await skip("worker_path_mismatch",
                          f"the worker serves {ontology_id} from {served['path'] or 'an unknown path'}, "
                          f"not {paths['worker_served']}")

    # Remote transport: the served file on the worker's host, as its
    # transfer service hashes it now. Central's own copy is never consulted.
    remote_served = None
    if remote is not None:
        remote_served = await remote.served()
        if remote_served["state"] == "unreachable":
            return await skip("transfer_unreachable", remote_served["message"])
        if remote_served["state"] == "missing":
            return await skip("served_file_missing",
                              f"the transfer service reports no {paths['worker_served']} on the worker's host")
        if remote_served["state"] != "ok":
            return await skip("transfer_refused", remote_served["message"])
        if remote_served.get("worker_path") != paths["worker_served"]:
            return await skip("transfer_path_mismatch",
                              f"the transfer service serves {remote_served.get('worker_path')}, "
                              f"not {paths['worker_served']}")
        # A stage kept after an earlier complete update is removed before
        # anything new starts on the host.
        pending = entry.get("transfer_release_pending")
        if pending:
            reason = await _release_pending(redis_client, ontology_id, remote, remote_served, pending)
            if reason:
                return await skip("transfer_cleanup_pending", reason)
            entry.pop("transfer_release_pending", None)
            remote_served["open_transactions"] = [
                t for t in remote_served.get("open_transactions") or [] if t != pending.get("txn")]
        open_txns = remote_served.get("open_transactions") or []
        if open_txns:
            return await skip("transfer_transaction_open",
                              f"the transfer service has unfinished transactions of {ontology_id}: "
                              f"{', '.join(map(str, open_txns))}; finish or abort them as described in "
                              "transfer_service/README.md (Recovery)")

    try:
        has_index = bool(await es_mgr.get_alias_target(ontology_id))
    except ESUnavailable as e:
        return await _fail(redis_client, ontology_id, "check_failed", "es_unavailable", str(e))

    async with aiohttp.ClientSession() as session:
        version_info = await check_version(
            source_url, entry.get("source_etag"), entry.get("source_last_modified"),
            entry.get("source_md5"), session, headers=headers)
    if version_info.get("error") == "source_gone":
        return await _fail(redis_client, ontology_id, "source_gone", "source_gone", "Source URL returned 404/410")
    if version_info.get("error"):
        return await _fail(redis_client, ontology_id, "check_failed", version_info["error"],
                           f"version check of the source failed: {version_info['error']}")
    # The stored markers describe the last complete update. With remote
    # transport they are trusted only while the worker's host still serves
    # the file that update recorded, and central's own copy, if it keeps
    # one, holds it too; otherwise the download below repairs it.
    markers_current = True
    if remote_served is not None:
        markers_current = remote_served["md5"] == entry.get("source_md5")
        if markers_current and not force and not version_info.get("changed") and has_index:
            central_md5 = await asyncio.to_thread(_central_copy_md5, paths)
            markers_current = central_md5 in (None, entry.get("source_md5"))
    if not force and not version_info.get("changed") and has_index and markers_current:
        logger.info("%s: source unchanged (ETag/Last-Modified), no update needed", ontology_id)
        await _record_unchanged(redis_client, ontology_id, version_info, entry.get("source_md5"))
        return _outcome("unchanged", message="source ETag/Last-Modified unchanged")

    txn = new_txn_id()
    paths = ontology_paths(ontologies_dir, ontology_id, txn, remote=remote is not None)
    record = {"id": txn, "kind": "update", "phase": "download", "worker": server_url,
              "staging": paths["worker_staging"]}
    if remote is not None:
        record.update(transport="remote", transfer=remote.target.transfer_url,
                      base_sha256=remote_served["sha256"])
    await _txn_begin(redis_client, ontology_id, record)
    return await _guarded(redis_client, ontology_id, txn, lambda: _update_txn(
        ontology_id, entry, redis_client, es_mgr, ontologies_dir, force, txn, paths,
        server_url, source_url, headers, version_info, has_index, remote, remote_served))


async def _update_txn(ontology_id, entry, redis_client, es_mgr, ontologies_dir, force, txn, paths,
                      server_url, source_url, headers, version_info, has_index,
                      remote=None, remote_served=None):
    secret_key = _secret(entry)
    # The reasoner the worker reports it was configured with (status poll).
    reasoner = entry.get("reasoner_configured") or entry.get("reasoner_type")
    if reasoner not in ("elk", "structural", "hermit"):
        reasoner = None  # "unknown" or absent: the worker keeps the current one
    staging_host = str(paths["staging"])
    staging_worker = paths["worker_staging"]
    served_worker = paths["worker_served"]
    remote_stage = {"sent": False}

    async def fail(update_status, error, message):
        # Only for failures where nothing in use remains: clean up and end.
        await _checkpoint(redis_client)
        if remote is not None and remote_stage["sent"]:
            aborted = await remote.abort(txn)
            await _checkpoint(redis_client)
            if not aborted["ok"]:
                return await recover("transfer_cleanup_unknown",
                                     f"{error}: {message}; the transfer service did not confirm removing "
                                     f"{staging_worker}: {aborted['message']}")
        _remove_txn_dir(paths)
        return await _fail(redis_client, ontology_id, update_status, error, message, txn=txn)

    async def recover(error, message):
        return await _recovery(redis_client, ontology_id, txn, error, message)

    # Download. download_ontology removes gzip itself, with its limits.
    async with aiohttp.ClientSession(auto_decompress=False) as session:
        dl_result = await download_ontology(source_url, staging_host, session, headers=headers)
    if "error" in dl_result:
        return await fail("download_failed", f"download: {dl_result['error']}", dl_result["error"])
    new_md5 = dl_result["md5"]

    # Unchanged by content: the download is the release of the last complete
    # update (recorded MD5). With no recorded hash yet (first check after a
    # source was configured), compare with the served file, so configuring a
    # source does not reclassify an ontology that is already current; but not
    # after a run stopped for recovery, which may have replaced the served
    # file without finishing. A never indexed ontology still goes through,
    # to get its index. With remote transport the worker's host must also
    # serve that release; a host or registry that disagrees gets the full
    # update, even with identical bytes, which also moves the search alias.
    recorded_md5 = entry.get("source_md5")
    if remote is None:
        if recorded_md5:
            unchanged = new_md5 == recorded_md5
        else:
            unchanged = (not _unfinished_since_success(entry)
                         and new_md5 == await asyncio.to_thread(file_md5, str(paths["served"])))
    else:
        unchanged = new_md5 == remote_served["md5"] and (
            new_md5 == recorded_md5 if recorded_md5 else not _unfinished_since_success(entry))
    if not force and unchanged and has_index:
        logger.info("%s: MD5 unchanged (%s), no update needed", ontology_id, new_md5)
        warnings: List[str] = []
        await _checkpoint(redis_client)
        if remote is not None:
            # Central's own copy follows the verified release here too.
            mirror = await asyncio.to_thread(_refresh_central_copy, paths, staging_host, new_md5)
            if mirror:
                warnings.append(mirror)
        _remove_txn_dir(paths)
        await _record_unchanged(redis_client, ontology_id, version_info, new_md5, txn=txn, warnings=warnings)
        extra = {"warnings": warnings} if warnings else {}
        return _outcome("unchanged", message="downloaded file identical to the served one", new_md5=new_md5,
                        **extra)

    new_sha256 = None
    if remote is not None:
        # Put the release where the worker will look for it, on its host.
        new_sha256, size = await asyncio.to_thread(update_transfer.sha256_file, staging_host)
        await _txn_set(redis_client, ontology_id, txn, phase="transfer", sha256=new_sha256)
        remote_stage["sent"] = True
        staged = await remote.stage(txn, staging_host, new_sha256, size, remote_served["sha256"])
        if staged["state"] == "unknown":
            return await recover("transfer_unknown", staged["message"])
        if staged["state"] != "staged":
            return await fail("transfer_failed", "transfer_failed", staged["message"])

    async with _worker_lock(server_url):
        blocked = await worker_recovery_blocker(redis_client, server_url, txn)
        if blocked:
            return await fail("worker_busy", "worker_recovery_required", blocked)
        # Validate with the same import policy a hot-swap uses, and export
        # the merged ontology that indexing will read.
        await _txn_set(redis_client, ontology_id, txn, phase="validate")
        validation = await validate_staged(server_url, staging_worker, ontology_id, secret_key,
                                           served_worker, export_id=txn)
        if not validation["ok"]:
            if not validation["answered"]:
                # A timed-out parser can still be reading staging and using
                # heap. Preserve its input and block this worker until the
                # operator confirms it has stopped.
                return await recover("validation_unknown", validation["message"])
            return await fail("validation_failed", "owl_validation_failed", validation["message"])
        export_path = validation.get("exportPath")
        class_count = validation.get("classCount")
        if not export_path or not isinstance(class_count, int):
            return await fail("validation_failed", "worker_outdated",
                              "the worker returned no export; update its validateOntology.groovy")

        # A new index of this transaction; the alias target is never touched.
        new_index = CentralESManager.transaction_index_name(ontology_id, txn)
        await _txn_set(redis_client, ontology_id, txn, phase="index", index=new_index, export=export_path)
        if not await es_mgr.create_class_index(new_index):
            await _checkpoint(redis_client)
            await discard_export(server_url, secret_key, ontology_id, txn)
            await _checkpoint(redis_client)
            await es_mgr.delete_index(new_index)
            return await fail("indexing_failed", "es_index_create_failed", f"could not create {new_index}")

        await _checkpoint(redis_client)
        started = await start_indexing(server_url, secret_key, ontology_id, export_path, new_index,
                                       entry.get("name", ontology_id), entry.get("description", ""))
        await _checkpoint(redis_client)
        if started["state"] == "refused":
            await discard_export(server_url, secret_key, ontology_id, txn)
            await _checkpoint(redis_client)
            await es_mgr.delete_index(new_index)
            return await fail("indexing_failed", "indexing_trigger_failed", started["message"])
        if started["state"] != "started":
            return await recover("indexing_unknown", started["message"])
        await _txn_set(redis_client, ontology_id, txn, task_id=started["task_id"], task_kind="index")
        indexing = await poll_task(server_url, started["task_id"])
        # A poll can last until HOTSWAP_TIMEOUT: check ownership before acting on it.
        await _checkpoint(redis_client)
        if indexing["status"] == "failed":
            # The indexer exited, and the worker deleted the export.
            await es_mgr.delete_index(new_index)
            return await fail("indexing_failed", "es_indexing_failed", f"ES indexing failed: {indexing['message']}")
        if indexing["status"] != "success":
            return await recover("indexing_unknown", f"ES indexing {indexing['status']}: {indexing['message']}")

        count = await es_mgr.count_documents(new_index)
        if count != class_count:
            await _checkpoint(redis_client)
            await es_mgr.delete_index(new_index)
            return await fail("indexing_failed", "index_incomplete",
                              f"{new_index} holds {count} documents for {class_count} validated classes")

        # Hot-swap
        await _txn_set(redis_client, ontology_id, txn, phase="hotswap", task_id=None)
        started = await start_hotswap(server_url, secret_key, ontology_id, staging_worker, served_worker, reasoner)
        await _checkpoint(redis_client)
        if started["state"] == "refused":
            await es_mgr.delete_index(new_index)
            return await fail("hotswap_failed", "hotswap_trigger_failed", started["message"])
        if started["state"] != "started":
            return await recover("hotswap_unknown", started["message"])
        await _txn_set(redis_client, ontology_id, txn, task_id=started["task_id"], task_kind="hotswap")
        swap = await poll_task(server_url, started["task_id"])
        await _checkpoint(redis_client)
        if swap["status"] in ("timeout", "lost"):
            return await recover("hotswap_unknown", f"hot-swap {swap['status']}: {swap['message']}")
        if swap["status"] == "failed":
            message = f"hot-swap failed: {swap['message']}"
            serving = swap["data"].get("serving")
            if serving is True:
                # The previous copy kept serving (an import catalogue reads the
                # new copy before it unloads the old one).
                await es_mgr.delete_index(new_index)
                return await fail("hotswap_failed", "hotswap_failed", message + "; the previous copy is serving")
            # Unloaded (or a worker that does not say): reload the served file.
            await _txn_set(redis_client, ontology_id, txn, phase="restore", task_id=None)
            restore = await start_hotswap(server_url, secret_key, ontology_id, served_worker, served_worker, reasoner)
            if restore["state"] != "started":
                return await recover("restore_failed", f"{message}; reload of the served file: {restore['message']}")
            await _txn_set(redis_client, ontology_id, txn, task_id=restore["task_id"], task_kind="restore")
            restored = await poll_task(server_url, restore["task_id"])
            await _checkpoint(redis_client)
            if restored["status"] != "success":
                return await recover("restore_failed",
                                     f"{message}; reload of the served file {restored['status']}: {restored['message']}")
            await es_mgr.delete_index(new_index)
            return await fail("hotswap_failed", "hotswap_failed", message + "; the served file was reloaded")

        # From here the worker serves the new release from the staging file.
        # A failure leaves the transaction for recovery; nothing is undone.
        await _txn_set(redis_client, ontology_id, txn, phase="promote", task_id=None)
        promoted = None
        if remote is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            try:
                # Whole-file copies: off the event loop.
                archived = await asyncio.to_thread(_archive_served, paths, ontology_id, timestamp)
                await asyncio.to_thread(_promote, paths)
            except OSError as e:
                return await recover("promote_failed",
                                     f"the worker serves the new release from {staging_worker}, "
                                     f"but {paths['served']} was not replaced: {e}")
        else:
            promoted = await remote.promote(txn, new_sha256, remote_served["sha256"])
            if promoted["state"] != "promoted":
                return await recover(
                    "promote_failed" if promoted["state"] == "refused" else "promote_unknown",
                    f"the worker serves the new release from {staging_worker}, but the transfer service "
                    f"did not confirm that {served_worker} holds it: {promoted['message']}")
            archived = f"/data/{promoted['archive']}" if promoted.get("archive") else None

        await _txn_set(redis_client, ontology_id, txn, phase="finalize")
        final = await finalize_served_path(server_url, secret_key, ontology_id, staging_worker, served_worker)
        await _checkpoint(redis_client)
        if not final["ok"]:
            return await recover("finalize_failed",
                                 f"{served_worker} holds the new release, but the worker still records "
                                 f"{final.get('path') or staging_worker}: {final['message']}")

    # The worker no longer refers to the staging file.
    warnings = []
    if remote is not None:
        released = await remote.release(txn, new_sha256)
        # Before central's copy and download are touched.
        await _checkpoint(redis_client)
        if not released["ok"]:
            # The worker no longer reads the stage, but the service keeps
            # the transaction open, which blocks the next one on the host.
            # Recorded now, so the next run removes it first.
            warnings.append(f"the transfer service kept {staging_worker}: {released['message']}; "
                            "the next update removes it first")
            await _set_fields(redis_client, ontology_id, transfer_release_pending={
                "txn": txn, "sha256": new_sha256, "transfer": remote.target.transfer_url, "since": _now()})
        mirror = await asyncio.to_thread(_refresh_central_copy, paths, staging_host)
        if mirror:
            warnings.append(mirror)
    _remove_txn_dir(paths)

    await _txn_set(redis_client, ontology_id, txn, phase="alias")
    old_index = await es_mgr.get_alias_target(ontology_id)
    if await _move_alias(es_mgr, ontology_id, new_index, old_index) == "unchanged":
        return await recover("alias_swap_failed",
                             f"the worker serves the new release, but search still uses {old_index}; "
                             f"{new_index} holds the new release")

    await _checkpoint(redis_client)
    current = await _load_entry(redis_client, ontology_id)
    await _retire_previous(es_mgr, ontology_id, current, [new_index, old_index])

    if remote is None:
        dependents = find_catalogue_dependents(ontologies_dir, served_worker)
    else:
        # The catalogues the worker reads are on its host.
        dependents = sorted(d for d in promoted.get("catalogue_dependents") or [] if isinstance(d, str))
    await _record_update(
        redis_client, ontology_id, txn=txn, version_info=version_info, new_md5=new_md5,
        new_index=new_index, old_index=old_index, worker_path=served_worker,
        archived=str(archived) if archived else None, validation=validation, dependents=dependents,
        warnings=warnings,
        transport={"transport": "remote", "sha256": new_sha256} if remote is not None else None,
    )
    if dependents:
        await _mark_dependents_stale(redis_client, ontology_id, dependents)
        logger.warning(
            "%s updated; ontologies whose import catalogue lists it keep the old copy until reloaded: %s",
            ontology_id, ", ".join(dependents),
        )
    for warning in warnings:
        logger.warning("%s: %s", ontology_id, warning)
    logger.info("Update pipeline complete for %s", ontology_id)
    extra = {"warnings": warnings} if warnings else {}
    return _outcome("updated", new_md5=new_md5, es_index=new_index, dependents=dependents, txn=txn, **extra)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _archive_served(paths: Dict[str, Any], ontology_id: str, timestamp: str) -> Optional[Path]:
    """Copy the served file into archive/ and keep the newest ARCHIVE_KEEP
    copies. The served file stays in place until _promote replaces it."""
    archive_dir = paths["archive"]
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"{ontology_id}_{timestamp}.owl"
    shutil.copy2(paths["served"], archived)
    logger.info("Archived previous OWL to %s", archived)
    # Only files this pipeline wrote, by name; the timestamp sorts by age.
    ours = sorted(p for p in archive_dir.iterdir()
                  if p.name.startswith(f"{ontology_id}_") and _ARCHIVE_NAME.match(p.name))
    for old in ours[:-ARCHIVE_KEEP]:
        try:
            old.unlink()
        except OSError as e:
            logger.warning("Could not prune archive %s: %s", old, e)
    return archived


def _promote(paths: Dict[str, Any]) -> None:
    """Atomically replace the served file with the staging file's content.
    The staging file stays in place: the worker reads it until finalize."""
    served = paths["served"]
    part = served.with_name(served.name + ".promote")
    try:
        os.unlink(part)
    except FileNotFoundError:
        pass
    try:
        os.link(paths["staging"], part)  # same file system: no copy
    except OSError:
        shutil.copy2(paths["staging"], part)
    os.replace(part, served)


def _central_copy_md5(paths: Dict[str, Any]) -> Optional[str]:
    """MD5 of central's own copy of the served file, None when it keeps none."""
    served = paths["served"]
    if served.is_symlink() or not served.is_file():
        return None
    return file_md5(str(served))


def _refresh_central_copy(paths: Dict[str, Any], download: str, md5: Optional[str] = None) -> Optional[str]:
    """Remote transport, once the worker's host was seen to serve the
    release in download: central's own copy (what central offers for
    download) follows it. Only an existing regular file is replaced, and
    with md5 given only when it differs; nothing is archived here, the
    worker's host keeps the archive. Returns a warning, or None. A copy left
    behind is retried by the next check, which compares it again."""
    served = paths["served"]
    if served.is_symlink() or not served.is_file():
        return None
    if md5 is not None and file_md5(str(served)) == md5:
        return None
    part = served.with_name(served.name + ".promote")
    try:
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        try:
            os.link(download, part)
        except OSError:
            shutil.copyfile(download, part)
        os.replace(part, served)
    except OSError as e:
        try:
            os.unlink(part)
        except OSError:
            pass
        return f"central's copy {served} still holds the previous release: {e}; the next check retries"
    return None


async def _release_pending(redis_client, ontology_id: str, remote, remote_served: Dict[str, Any],
                           pending: Dict[str, Any]) -> Optional[str]:
    """Remove the stage an earlier complete update could not remove
    (transfer_release_pending). Safe because the caller has just seen the
    worker report the served path for the ontology (it no longer records the
    stage), and the service removes it only while the served file holds
    that transaction's release. Returns why it was not removed, or None."""
    txn, sha256 = pending.get("txn"), pending.get("sha256")
    remedy = ("After reconciling the worker and the transfer service, POST resolve_update with "
              "clear_transfer_cleanup=true, the matching txn_id, reconciled=true and a note; "
              "this clears only the marker and deletes no files.")
    if pending.get("transfer") != remote.target.transfer_url:
        return f"transaction {txn} belongs to a different transfer service. {remedy}"
    current = await remote.status(txn)
    if current["state"] != "ok":
        return f"transaction {txn} cleanup status is {current['state']}: {current.get('message', '')}. {remedy}"
    if current.get("sha256") != sha256:
        return f"transaction {txn} has different release parameters on the service. {remedy}"
    if current.get("record_state") == "finalized":
        # An operator may have finalized before rolling back, or with the
        # explicit served_changed_ok override. There is nothing left to delete.
        await _set_fields(redis_client, ontology_id, transfer_release_pending=None)
        return None
    if remote_served.get("sha256") != sha256:
        return (f"transaction {txn} kept its stage after its update, and the worker's host no longer serves "
                f"that release ({remote_served.get('sha256')}, not {sha256}); it is not removed automatically. "
                f"{remedy}")
    if current.get("observed") != "promoted":
        return f"transaction {txn} is {current.get('observed')}, not a confirmed promotion. {remedy}"
    await _checkpoint(redis_client)
    released = await remote.release(txn, sha256)
    if not released["ok"]:
        return f"the stage of transaction {txn} could not be removed: {released['message']}"
    await _set_fields(redis_client, ontology_id, transfer_release_pending=None)
    logger.info("%s: removed the stage kept by transaction %s", ontology_id, txn)
    return None


def clear_transfer_cleanup_entry(entry: Dict[str, Any], txn: str, note: str) -> bool:
    """Clear the cleanup marker of txn from entry (in place), with an audit
    event. False when there is an update transaction, no matching marker or
    no note."""
    pending = entry.get("transfer_release_pending")
    if entry.get("update_txn") or not pending or pending.get("txn") != txn or not note.strip():
        return False
    entry.pop("transfer_release_pending")
    _append_history(entry, {"timestamp": _now(), "status": "transfer_cleanup_resolved",
                            "txn": txn, "note": note, "transfer": pending.get("transfer")})
    return True


async def resolve_transfer_cleanup(redis_client, ontology_id: str, txn: str, note: str) -> bool:
    """Clear only a reconciled cleanup marker. Never delete host files or
    resolve an active update transaction; preserve a separate audit event."""
    lock = _lock_for(ontology_id)
    if lock.locked():
        return False
    async with lock:
        entry = await _load_entry(redis_client, ontology_id)
        if not clear_transfer_cleanup_entry(entry, txn, note):
            return False
        await _save_entry(redis_client, ontology_id, entry)
        return True



# ---------------------------------------------------------------------------
# Registry records of a successful update
# ---------------------------------------------------------------------------

def _imports_change(previous: Optional[List[str]], current: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if current is None:
        return None  # a worker that predates the report
    if previous is None:
        return {"baseline": True, "declared": current}
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    if not added and not removed:
        return None
    return {"added": added, "removed": removed}


async def _record_update(redis_client, ontology_id, *, txn, version_info, new_md5, new_index, old_index,
                         worker_path, archived, validation, dependents, warnings=None, transport=None) -> None:
    # Re-read: the status poller may have written the entry during the update.
    entry = await _load_entry(redis_client, ontology_id)
    now = _now()
    declared = validation.get("declaredImports")
    change = _imports_change(entry.get("source_declared_imports"), declared)
    entry.update({
        # Version markers move only with a complete update, so a run that
        # stopped part way is never taken for "unchanged" later.
        "source_etag": version_info.get("etag"),
        "source_last_modified": version_info.get("last_modified"),
        "source_md5": new_md5,
        "last_checked": now,
        "last_updated": now,
        "last_check_status": "updated",
        "last_check_error": None,
        "active_owl_path": worker_path,
        "active_es_index": new_index,
        "previous_es_index": old_index,
        "update_imports_policy": validation.get("importsPolicy"),
        "catalogue_dependents": dependents,
    })
    if declared is not None:
        entry["source_declared_imports"] = declared
    if change and not change.get("baseline"):
        entry["imports_changed"] = {"at": now, **change}
        logger.warning("%s: declared imports changed: %s", ontology_id, change)
    if archived:
        entry["archived_owl_path"] = archived
    if version_info.get("permanent_redirect"):
        entry["source_resolved_url"] = version_info["permanent_redirect"]
    # This ontology was reloaded with its catalogue files as they are now.
    entry.pop("stale_imports", None)
    if (entry.get("update_txn") or {}).get("id") == txn:
        entry.pop("update_txn")
    _set_update_status(entry, "ok", None)
    item = {"timestamp": now, "md5": new_md5, "status": "success", "txn": txn, "es_index": new_index,
            "documents": validation.get("classCount")}
    if change and not change.get("baseline"):
        item["imports_changed"] = change
    if transport:
        item.update(transport)
    if warnings:
        entry["update_warnings"] = warnings
        item["warnings"] = warnings
    else:
        entry.pop("update_warnings", None)
    _append_history(entry, item)
    await _save_entry(redis_client, ontology_id, entry)


async def _mark_dependents_stale(redis_client, ontology_id, dependents) -> None:
    now = _now()
    for dep in dependents:
        raw = await redis_client.hget("registered_servers", dep)
        if not raw:
            continue
        entry = json.loads(raw)
        stale = entry.get("stale_imports") or {}
        stale[ontology_id] = now
        entry["stale_imports"] = stale
        await _save_entry(redis_client, dep, entry)


async def _touch_last_checked(redis_client, ontology_id: str) -> None:
    raw = await redis_client.hget("registered_servers", ontology_id)
    if raw:
        entry = json.loads(raw)
        entry["last_checked"] = _now()
        await redis_client.hset("registered_servers", ontology_id, json.dumps(entry))
