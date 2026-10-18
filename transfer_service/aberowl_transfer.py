"""
AberOWL ontology transfer service.

A small host-side companion for central's ontology update pipeline when
central and a worker do not share a file system. It runs on the worker's
host, outside the reasoning containers, and writes the host directory those
containers mount read-only at /data. It has no access to the workers, Docker
or anything else on the host.

It receives a staged release of a configured ontology, verifies it, and on
request makes it the served file, keeping the file it replaces. Every path
is derived from a configured ontology id and central's transaction id; no
request names a path.

  <data_root>/<id>/<id>.owl                  the served file (/data/<id>/<id>.owl for the worker)
  <data_root>/<id>/updates/<txn>/<id>.owl    a staged release
  <data_root>/<id>/archive/<id>_<txn>.owl    the served file a transaction replaced
  <state_dir>/journal/<id>/<txn>.json        the transaction's journal record
  <state_dir>/locks/<id>.lock                one operation per ontology at a time (flock)

A transaction is prepared (a compare-and-set on the served file's SHA-256),
staged (the upload, verified by size and SHA-256 before it becomes visible),
promoted (the served file is archived and atomically replaced; the stage is
kept, because the worker reads it until central finalizes), and then either
finalized (the stage is removed) or, if it was never promoted, aborted. The
service never removes anything on its own: central asks, and only for the
transaction's own files. Every state is journalled before the files change,
so an interrupted operation is completed or refused on the next request.

API, all under /v1 and authenticated with a bearer token:

  GET  /v1/ontologies/{id}/served                        the served file's hashes
  PUT  /v1/ontologies/{id}/transactions/{txn}            prepare
  PUT  /v1/ontologies/{id}/transactions/{txn}/file       upload the release
  GET  /v1/ontologies/{id}/transactions/{txn}            journal record and the files as they are
  POST /v1/ontologies/{id}/transactions/{txn}/promote
  POST /v1/ontologies/{id}/transactions/{txn}/finalize   remove the stage once the worker no longer reads it
  POST /v1/ontologies/{id}/transactions/{txn}/abort      remove a stage that was never promoted

See transfer_service/README.md.
"""

import asyncio
import contextlib
import errno
import fcntl
import hashlib
import hmac
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

logger = logging.getLogger("aberowl_transfer")

# The worker's rule for ontology ids, and central's for transaction ids.
ONTOLOGY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TXN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20
MIN_TOKEN_LENGTH = 32
# A transaction in one of these states may still write the ontology's
# directory, or (promoted) has a stage the worker may still read: only one
# may exist per ontology. A promoted transaction is closed by finalize, which
# central sends once the worker reports the served path instead of the stage.
OPEN_STATES = ("prepared", "staged", "promoting", "promoted")
# JSON request bodies are small; anything larger is refused unread.
JSON_BODY_LIMIT = 16 << 10
DEFAULTS: Dict[str, Any] = {
    "max_bytes": 8 << 30,
    "min_free_bytes": 1 << 30,
    "archive_keep": 3,
    "worker_data_root": "/data",
    "lock_timeout_seconds": 30,
}
CONFIG_KEYS = {"data_root", "state_dir", "ontologies", *DEFAULTS}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """The configuration is unusable. The service does not start."""


@dataclass(frozen=True)
class Config:
    data_root: Path
    state_dir: Path
    ontologies: FrozenSet[str]
    token: bytes = field(repr=False)
    max_bytes: int = DEFAULTS["max_bytes"]
    min_free_bytes: int = DEFAULTS["min_free_bytes"]
    archive_keep: int = DEFAULTS["archive_keep"]
    worker_data_root: str = DEFAULTS["worker_data_root"]
    lock_timeout: float = DEFAULTS["lock_timeout_seconds"]


def _real_dir(value: Any, name: str) -> Path:
    """An existing, writable directory given by its real absolute path."""
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ConfigError(f"{name} must be an absolute path")
    path = Path(os.path.normpath(value))
    try:
        st = os.lstat(path)
    except OSError as e:
        raise ConfigError(f"{name} {path}: {e.strerror}") from None
    if not stat.S_ISDIR(st.st_mode):
        raise ConfigError(f"{name} {path} is not a directory (symlinks are refused)")
    if Path(os.path.realpath(path)) != path:
        raise ConfigError(f"{name} {path} is reached through a symlink; give its real path")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ConfigError(f"{name} {path} is not writable by this user")
    return path


def _integer(raw: Mapping[str, Any], key: str, minimum: int) -> int:
    value = raw.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{key} must be an integer of at least {minimum}")
    return value


def read_token(env: Mapping[str, str]) -> bytes:
    """The bearer token, from ABEROWL_TRANSFER_TOKEN_FILE (preferred) or
    ABEROWL_TRANSFER_TOKEN. Never logged or returned."""
    path = env.get("ABEROWL_TRANSFER_TOKEN_FILE", "")
    value = env.get("ABEROWL_TRANSFER_TOKEN", "")
    if path and value:
        raise ConfigError("set ABEROWL_TRANSFER_TOKEN_FILE or ABEROWL_TRANSFER_TOKEN, not both")
    if path:
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(f"cannot read the token file: {getattr(e, 'strerror', None) or type(e).__name__}") from None
    elif value:
        token = value.strip()
    else:
        raise ConfigError("no token: set ABEROWL_TRANSFER_TOKEN_FILE")
    if len(token) < MIN_TOKEN_LENGTH or any(c.isspace() for c in token) or not token.isprintable():
        raise ConfigError(f"the token must be at least {MIN_TOKEN_LENGTH} printable characters without spaces")
    return token.encode("utf-8")


def build_config(raw: Any, token: bytes) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("the configuration must be a JSON object")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"unknown configuration keys: {', '.join(unknown)}")
    data_root = _real_dir(raw.get("data_root"), "data_root")
    state_dir = _real_dir(raw.get("state_dir"), "state_dir")
    if data_root == state_dir or state_dir.is_relative_to(data_root) or data_root.is_relative_to(state_dir):
        raise ConfigError("state_dir and data_root must be separate trees: workers must not see the journal")

    ontologies = raw.get("ontologies")
    if not isinstance(ontologies, list) or not ontologies:
        raise ConfigError("ontologies must be a non-empty list of ontology ids")
    for oid in ontologies:
        if not isinstance(oid, str) or not ONTOLOGY_ID.match(oid):
            raise ConfigError(f"invalid ontology id: {oid!r}")
        try:
            st = os.lstat(data_root / oid)
        except OSError:
            raise ConfigError(f"ontology {oid} has no directory under data_root") from None
        if not stat.S_ISDIR(st.st_mode):
            raise ConfigError(f"{oid} under data_root is not a directory (symlinks are refused)")
        if not os.access(data_root / oid, os.R_OK | os.W_OK | os.X_OK):
            raise ConfigError(f"{oid} under data_root is not writable by this user")
    if len(set(ontologies)) != len(ontologies):
        raise ConfigError("ontologies lists an id twice")

    worker_root = raw.get("worker_data_root", DEFAULTS["worker_data_root"])
    if (not isinstance(worker_root, str) or not worker_root.startswith("/") or worker_root == "/"
            or posixpath.normpath(worker_root) != worker_root):
        raise ConfigError("worker_data_root must be a normalised absolute path such as /data")
    lock_timeout = raw.get("lock_timeout_seconds", DEFAULTS["lock_timeout_seconds"])
    if isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float)) or lock_timeout <= 0:
        raise ConfigError("lock_timeout_seconds must be a positive number")

    return Config(
        data_root=data_root,
        state_dir=state_dir,
        ontologies=frozenset(ontologies),
        token=token,
        max_bytes=_integer(raw, "max_bytes", 1),
        min_free_bytes=_integer(raw, "min_free_bytes", 0),
        archive_keep=_integer(raw, "archive_keep", 1),
        worker_data_root=worker_root,
        lock_timeout=float(lock_timeout),
    )


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    """Read the configuration named by ABEROWL_TRANSFER_CONFIG. Raises
    ConfigError for anything incomplete or unsafe."""
    env = os.environ if env is None else env
    path = env.get("ABEROWL_TRANSFER_CONFIG", "")
    if not path:
        raise ConfigError("ABEROWL_TRANSFER_CONFIG is not set")
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read the configuration {path}: {e}") from None
    return build_config(raw, read_token(env))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

class Refused(Exception):
    """A request that changes nothing, with the HTTP status that says why."""

    def __init__(self, status: int, error: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.error = error
        self.message = message
        self.extra = extra

    def response(self) -> JSONResponse:
        return JSONResponse({"error": self.error, "message": self.message, **self.extra}, status_code=self.status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lstat(path: Path) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _replace(src: Path, dst: Path) -> None:
    """The replace that makes a release the served file. A name of its own
    so tests can interrupt a promotion exactly here."""
    os.replace(src, dst)


def _link(src: Path, dst: Path) -> None:
    """The hard link tried first for archives and promotions. A name of its
    own so tests can exercise the copy used where links are not allowed."""
    os.link(src, dst)


def _public(digest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if digest is None:
        return None
    return {k: v for k, v in digest.items() if k != "key"}


def _check_sha(value: Any, name: str) -> None:
    if not isinstance(value, str) or not SHA256.match(value):
        raise Refused(400, "bad_request", f"{name} must be a lower-case hex SHA-256")


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

class TransferService:
    """The operations, synchronous; the HTTP layer runs them in threads.
    Each holds the ontology's lock file, so separate processes serialise too."""

    def __init__(self, config: Config):
        self.config = config
        self._digests: Dict[Tuple[int, int, int, int], Dict[str, Any]] = {}
        for sub in ("journal", "locks"):
            path = config.state_dir / sub
            with contextlib.suppress(FileExistsError):
                os.mkdir(path, 0o700)
            st = os.lstat(path)
            if not stat.S_ISDIR(st.st_mode):
                raise ConfigError(f"{path} is not a directory")

    # -- paths ------------------------------------------------------------

    def _rel(self, path: Path) -> str:
        return str(Path(path).relative_to(self.config.data_root))

    def worker_path(self, oid: str) -> str:
        return f"{self.config.worker_data_root}/{oid}/{oid}.owl"

    def _paths(self, oid: str, txn: Optional[str] = None) -> Dict[str, Path]:
        ont_dir = self.config.data_root / oid
        st = _lstat(ont_dir)
        if st is None or not stat.S_ISDIR(st.st_mode):
            raise Refused(409, "unsafe_path", f"{oid} is not a directory under the data root")
        paths = {
            "dir": ont_dir,
            "served": ont_dir / f"{oid}.owl",
            "updates": ont_dir / "updates",
            "archive_dir": ont_dir / "archive",
        }
        if txn:
            paths["stage_dir"] = ont_dir / "updates" / txn
            paths["stage"] = paths["stage_dir"] / f"{oid}.owl"
            paths["archive"] = ont_dir / "archive" / f"{oid}_{txn}.owl"
            paths["part"] = ont_dir / f".{oid}.owl.promote-{txn}"
        return paths

    def _subdir(self, parent: Path, name: str, create: bool) -> Optional[Path]:
        """A real directory parent/name, created when asked; never a symlink."""
        path = parent / name
        st = _lstat(path)
        if st is None:
            if not create:
                return None
            os.mkdir(path, 0o755)
            os.chmod(path, 0o755)  # readable by the worker whatever the umask
            _fsync_dir(parent)
            return path
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(409, "unsafe_path", f"{self._rel(path)} is not a directory")
        return path

    def _stage_dir(self, paths: Dict[str, Path]) -> Path:
        updates = self._subdir(paths["dir"], "updates", create=True)
        return self._subdir(updates, paths["stage_dir"].name, create=True)

    # -- hashes -----------------------------------------------------------

    def _digest(self, path: Path, cached: bool = True) -> Optional[Dict[str, Any]]:
        """SHA-256, MD5 and size of a regular file, or None when it does not
        exist. A symlink or anything else is refused. cached reuses a result
        for the same inode, size and modification time (status queries);
        every decision that changes a file hashes afresh."""
        st = _lstat(path)
        if st is None:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise Refused(409, "unsafe_path", f"{self._rel(path)} is not a regular file")
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if cached and key in self._digests:
            return self._digests[key]
        sha, md5 = hashlib.sha256(), hashlib.md5()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as f:
            opened = os.fstat(f.fileno())
            if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                raise Refused(409, "changed", f"{self._rel(path)} changed while it was read")
            for chunk in iter(lambda: f.read(CHUNK), b""):
                sha.update(chunk)
                md5.update(chunk)
            after = os.fstat(f.fileno())
        if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
            raise Refused(409, "changed", f"{self._rel(path)} changed while it was read")
        result = {"sha256": sha.hexdigest(), "md5": md5.hexdigest(), "size": st.st_size, "key": key}
        if len(self._digests) > 1024:
            self._digests.clear()
        self._digests[key] = result
        return result

    def _observed_digest(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return self._digest(path)
        except Refused as r:
            return {"error": r.error, "message": r.message}

    # -- journal and lock -------------------------------------------------

    def _journal(self, oid: str, txn: Optional[str] = None) -> Path:
        path = self.config.state_dir / "journal" / oid
        return path / f"{txn}.json" if txn else path

    def _read(self, oid: str, txn: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self._journal(oid, txn).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _require(self, oid: str, txn: str) -> Dict[str, Any]:
        record = self._read(oid, txn)
        if record is None:
            raise Refused(404, "unknown_transaction", f"no transaction {txn} for {oid}")
        return record

    def _write(self, record: Dict[str, Any]) -> None:
        directory = self._journal(record["ontology_id"])
        if _lstat(directory) is None:
            os.mkdir(directory, 0o700)
            _fsync_dir(directory.parent)
        record["updated"] = _now()
        _write_atomic(self._journal(record["ontology_id"], record["txn"]),
                      json.dumps(record, indent=1, sort_keys=True).encode("utf-8"))

    def _records(self, oid: str) -> List[Dict[str, Any]]:
        directory = self._journal(oid)
        if not directory.is_dir():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]

    @contextlib.contextmanager
    def _locked(self, oid: str):
        """flock on <state_dir>/locks/<id>.lock: excludes other threads and
        other processes using the same state directory."""
        fd = os.open(self.config.state_dir / "locks" / f"{oid}.lock",
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            deadline = time.monotonic() + self.config.lock_timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Refused(503, "busy", f"another operation on {oid} holds its lock") from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    # -- operations -------------------------------------------------------

    def served(self, oid: str) -> Tuple[int, Dict[str, Any]]:
        paths = self._paths(oid)
        digest = self._digest(paths["served"])
        if digest is None:
            raise Refused(404, "served_missing", f"{oid}/{oid}.owl does not exist")
        open_txns = [r["txn"] for r in self._records(oid) if r.get("state") in OPEN_STATES]
        return 200, {"ontology_id": oid, "worker_path": self.worker_path(oid), **_public(digest),
                     "open_transactions": open_txns, "catalogue_dependents": self._dependents(oid)}

    def prepare(self, oid: str, txn: str, sha256: Any, size: Any, base_sha256: Any) -> Tuple[int, Dict[str, Any]]:
        _check_sha(sha256, "sha256")
        _check_sha(base_sha256, "base_sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise Refused(400, "bad_request", "size must be a positive integer")
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._read(oid, txn)
            if record is not None:
                if (record["sha256"], record["size"], record["base_sha256"]) != (sha256, size, base_sha256):
                    raise Refused(409, "txn_conflict", f"transaction {txn} exists with other parameters")
                if record["state"] == "prepared":
                    self._stage_dir(paths)  # an interrupted prepare journalled it first
                    record = self._settle(record, paths)
                return 200, record
            if size > self.config.max_bytes:
                raise Refused(413, "too_large", f"{size} bytes exceed max_bytes {self.config.max_bytes}")
            open_txns = [r["txn"] for r in self._records(oid) if r.get("state") in OPEN_STATES]
            if open_txns:
                raise Refused(409, "other_transaction_open",
                              f"transaction {', '.join(open_txns)} of {oid} is not finished", transactions=open_txns)
            served = self._digest(paths["served"], cached=False)
            if served is None:
                raise Refused(409, "served_missing", f"{oid}/{oid}.owl does not exist")
            if served["sha256"] != base_sha256:
                raise Refused(409, "base_mismatch", f"{oid}/{oid}.owl holds {served['sha256']}, not {base_sha256}",
                              served_sha256=served["sha256"])
            if (self._subdir(paths["dir"], "updates", create=False) is not None
                    and os.path.lexists(paths["stage_dir"])):
                raise Refused(409, "unknown_artifact", f"{self._rel(paths['stage_dir'])} exists, but no journal record owns it")
            if shutil.disk_usage(paths["dir"]).free - size < self.config.min_free_bytes:
                raise Refused(507, "insufficient_space", f"{size} bytes would leave less than min_free_bytes free")
            record = {"txn": txn, "ontology_id": oid, "state": "prepared", "sha256": sha256, "size": size,
                      "base_sha256": base_sha256, "created": _now(), "stage": self._rel(paths["stage"]),
                      "archive": self._rel(paths["archive"])}
            # Journal first: every file this service creates is owned by a record.
            self._write(record)
            self._stage_dir(paths)
            logger.info("%s %s: prepared (%d bytes)", oid, txn, size)
            return 201, record

    def begin_upload(self, oid: str, txn: str) -> Tuple[Dict[str, Any], Path, int]:
        """Open a private temporary file in the transaction's directory."""
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._require(oid, txn)
            if record["state"] not in ("prepared", "staged"):
                raise Refused(409, "wrong_state", f"transaction {txn} is {record['state']}")
            stage_dir = self._stage_dir(paths)
            if shutil.disk_usage(stage_dir).free - record["size"] < self.config.min_free_bytes:
                raise Refused(507, "insufficient_space", "the upload would leave less than min_free_bytes free")
            tmp = stage_dir / f".upload-{secrets.token_hex(8)}.part"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
            os.fchmod(fd, 0o644)
            return record, tmp, fd

    def install(self, oid: str, txn: str, tmp: Path) -> Tuple[int, Dict[str, Any]]:
        """Make a verified upload the stage, by a link that never replaces
        an existing file. The temporary name is always removed."""
        try:
            with self._locked(oid):
                paths = self._paths(oid, txn)
                record = self._require(oid, txn)
                if record["state"] not in ("prepared", "staged"):
                    raise Refused(409, "wrong_state", f"transaction {txn} is {record['state']}")
                existing = self._digest(paths["stage"], cached=False)
                status = 200
                if existing is None:
                    os.link(tmp, paths["stage"])
                    _fsync_dir(paths["stage_dir"])
                    status = 201
                elif existing["sha256"] != record["sha256"]:
                    raise Refused(409, "stage_conflict", f"{self._rel(paths['stage'])} holds other content")
                if record["state"] != "staged":
                    record.update(state="staged", staged=_now())
                    self._write(record)
                    logger.info("%s %s: staged", oid, txn)
                return status, record
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

    def status(self, oid: str, txn: str) -> Tuple[int, Dict[str, Any]]:
        """The journal record, the files as they are now, and what they mean
        for the transaction ("observed")."""
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._settle(self._require(oid, txn), paths)
            stage = self._observed_digest(paths["stage"])
            served = self._observed_digest(paths["served"])
            return 200, {**record, "observed": self._observe(record, stage, served),
                         "stage_file": _public(stage), "served_file": _public(served)}

    def _settle(self, record: Dict[str, Any], paths: Dict[str, Path]) -> Dict[str, Any]:
        """A crash between installing a verified stage and journalling it
        leaves the record "prepared" beside a complete stage. The stage is
        hashed afresh and, if it is the declared release, the record says
        "staged" durably before anyone acts on it."""
        if record["state"] != "prepared":
            return record
        try:
            stage = self._digest(paths["stage"], cached=False)
        except Refused:
            return record  # not a regular file: status reports a conflict
        if stage is not None and stage["sha256"] == record["sha256"]:
            record.update(state="staged", staged=_now(), settled_after_interruption=True)
            self._write(record)
            logger.info("%s %s: staged (recorded after an interruption)", record["ontology_id"], record["txn"])
        return record

    @staticmethod
    def _replaced(record: Dict[str, Any], stage: Optional[Dict[str, Any]], served: Optional[Dict[str, Any]]) -> bool:
        """Whether this transaction's promotion replaced the served file.
        Equal content alone is not proof while the release equals the base
        (a forced update of an unchanged file): then only the identity of the
        file put in place, journalled before the replace, counts."""
        if served is None or "error" in served:
            return False
        state, new, base = record["state"], record["sha256"], record["base_sha256"]
        if state in ("promoted", "finalized"):
            return served["sha256"] == new  # the journal records the replace
        if state != "promoting":
            return False
        if record.get("promote_file") and list(served["key"][:2]) == record["promote_file"]:
            return True
        if stage is not None and "error" not in stage and served["key"][:2] == stage["key"][:2]:
            return True  # promoted by hard link before the identity was journalled
        return served["sha256"] == new and new != base

    def _observe(self, record: Dict[str, Any], stage: Optional[Dict[str, Any]],
                 served: Optional[Dict[str, Any]]) -> str:
        state, new, base = record["state"], record["sha256"], record["base_sha256"]
        if (stage and "error" in stage) or (served and "error" in served):
            return "conflict"
        if state == "finalized":
            return "finalized"
        if state == "aborted":
            return "aborted" if stage is None else "abort_incomplete"
        if state in ("promoting", "promoted") and self._replaced(record, stage, served):
            return "promoted"
        served_sha = served["sha256"] if served else None
        if state == "promoted" or served_sha != base:
            return "conflict"
        if stage is None:
            return "prepared"
        return "staged" if stage["sha256"] == new else "conflict"

    def _link_or_copy(self, src: Path, dst: Path, expected: Dict[str, Any]) -> None:
        """dst becomes src: a hard link where the file system allows it,
        otherwise a verified copy. Never replaces an existing dst."""
        try:
            _link(src, dst)
        except OSError as e:
            if e.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP):
                raise
            if shutil.disk_usage(dst.parent).free - expected["size"] < self.config.min_free_bytes:
                raise Refused(507, "insufficient_space", f"no space to copy {self._rel(src)}") from None
            tmp = dst.with_name(f".{dst.name}.{secrets.token_hex(4)}.tmp")
            try:
                shutil.copyfile(src, tmp, follow_symlinks=False)
                fd = os.open(tmp, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.chmod(tmp, 0o644)
                os.link(tmp, dst)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp)
            copied = self._digest(dst, cached=False)
            if copied is None or copied["sha256"] != expected["sha256"]:
                raise Refused(409, "changed", f"{self._rel(src)} changed while it was copied")
            return
        st = os.lstat(dst)
        if (st.st_dev, st.st_ino) != expected["key"][:2]:
            raise Refused(409, "changed", f"{self._rel(src)} changed while it was linked")

    def promote(self, oid: str, txn: str, sha256: Any, base_sha256: Any) -> Tuple[int, Dict[str, Any]]:
        """Archive the served file and replace it with the stage. Repeats are
        answered from the files; the served file is replaced only while it
        still holds base_sha256."""
        _check_sha(sha256, "sha256")
        _check_sha(base_sha256, "base_sha256")
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._require(oid, txn)
            if (record["sha256"], record["base_sha256"]) != (sha256, base_sha256):
                raise Refused(409, "txn_conflict", f"transaction {txn} exists with other parameters")
            record = self._settle(record, paths)
            state = record["state"]
            if state in ("promoted", "finalized"):
                served = self._digest(paths["served"], cached=False)
                if self._replaced(record, None, served):
                    return self._promoted(record, paths, served)
                raise Refused(409, "served_changed", "the served file changed after this transaction promoted it")
            if state not in ("staged", "promoting"):
                raise Refused(409, "wrong_state", f"transaction {txn} is {state}")
            stage = self._digest(paths["stage"], cached=False)
            if stage is None or stage["sha256"] != sha256:
                raise Refused(409, "stage_changed", f"{self._rel(paths['stage'])} is missing or holds other content")
            served = self._digest(paths["served"], cached=False)
            if served is None:
                raise Refused(409, "served_missing", f"{oid}/{oid}.owl does not exist")
            if state == "promoting" and self._replaced(record, stage, served):
                return self._promoted(record, paths, served)  # interrupted after the replace
            if served["sha256"] != base_sha256:
                raise Refused(409, "served_changed",
                              f"{oid}/{oid}.owl holds {served['sha256']}, not the base {base_sha256}",
                              served_sha256=served["sha256"])
            if state == "staged":
                record.update(state="promoting", promoting=_now())
                self._write(record)

            archive_dir = self._subdir(paths["dir"], "archive", create=True)
            archived = self._digest(paths["archive"], cached=False)
            if archived is None:
                self._link_or_copy(paths["served"], paths["archive"], served)
                _fsync_dir(archive_dir)
            elif archived["sha256"] != base_sha256:
                raise Refused(409, "archive_conflict", f"{self._rel(paths['archive'])} holds other content")

            st = os.lstat(paths["served"])
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != served["key"]:
                raise Refused(409, "served_changed", "the served file changed during the promotion")
            part = paths["part"]
            leftover = _lstat(part)
            if leftover is not None:
                if stat.S_ISDIR(leftover.st_mode):
                    raise Refused(409, "unsafe_path", f"{self._rel(part)} is a directory")
                os.unlink(part)
            self._link_or_copy(paths["stage"], part, stage)
            # The identity of the file about to become the served file is
            # journalled first: after an interruption it, not equal content,
            # shows whether the replace happened.
            placed = os.lstat(part)
            record["promote_file"] = [placed.st_dev, placed.st_ino]
            self._write(record)
            _replace(part, paths["served"])
            _fsync_dir(paths["dir"])
            now = self._digest(paths["served"], cached=False)
            if now is None or now["sha256"] != sha256 or list(now["key"][:2]) != record["promote_file"]:
                raise RuntimeError(f"{oid}/{oid}.owl does not hold the release after the replace")
            logger.info("%s %s: promoted; previous file kept as %s", oid, txn, record["archive"])
            return self._promoted(record, paths, now)

    def _promoted(self, record: Dict[str, Any], paths: Dict[str, Path],
                  served: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if record["state"] == "promoting":
            record.update(state="promoted", promoted=_now())
            self._write(record)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(paths["part"])
        return 200, {**record, "served_file": _public(served),
                     "catalogue_dependents": self._dependents(record["ontology_id"])}

    def finalize(self, oid: str, txn: str, sha256: Any, served_changed_ok: bool = False) -> Tuple[int, Dict[str, Any]]:
        """Remove the stage of a promoted transaction. Central asks only after
        the worker reported the served path in place of the stage.

        served_changed_ok is for an operator only: after a promotion the
        served file was changed by hand (for instance a rollback), and the
        operator has confirmed that the worker no longer records the stage.
        The transaction must have been promoted; its stage is removed even
        though the served file no longer holds its release."""
        _check_sha(sha256, "sha256")
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._require(oid, txn)
            if record["sha256"] != sha256:
                raise Refused(409, "txn_conflict", f"transaction {txn} exists with other parameters")
            if record["state"] == "finalized":
                return 200, record
            if record["state"] not in ("promoting", "promoted"):
                raise Refused(409, "wrong_state", f"transaction {txn} is {record['state']}")
            served = self._digest(paths["served"], cached=False)
            stage = self._digest(paths["stage"], cached=False)
            if served_changed_ok:
                if record["state"] != "promoted":
                    raise Refused(409, "wrong_state", "only a promotion the journal confirms can be closed this way")
            else:
                if served is None or served["sha256"] != sha256:
                    raise Refused(409, "served_changed", "the served file does not hold this release; the stage is kept")
                if not self._replaced(record, stage, served):
                    raise Refused(409, "wrong_state", "the promotion is not confirmed; promote again first")
            left = self._remove_stage(paths)
            record.update(state="finalized", finalized=_now())
            record.setdefault("promoted", record["finalized"])
            if served_changed_ok:
                record["closed_with_served_changed"] = record["finalized"]
            self._write(record)
            pruned = self._prune_archives(oid)
            logger.info("%s %s: finalized; stage removed", oid, txn)
            return 200, {**record, "left_in_place": left, "pruned_archives": pruned}

    def abort(self, oid: str, txn: str) -> Tuple[int, Dict[str, Any]]:
        """Remove the stage of a transaction that was never promoted. The
        served file is not touched; a promotion is never undone here."""
        with self._locked(oid):
            paths = self._paths(oid, txn)
            record = self._require(oid, txn)
            if record["state"] not in ("prepared", "staged", "aborted"):
                raise Refused(409, "cannot_abort",
                              f"transaction {txn} is {record['state']}; a promotion is not undone automatically")
            left = self._remove_stage(paths)
            if record["state"] != "aborted":
                record.update(state="aborted", aborted=_now())
                self._write(record)
                logger.info("%s %s: aborted; stage removed", oid, txn)
            return 200, {**record, "left_in_place": left}

    def _remove_stage(self, paths: Dict[str, Path]) -> List[str]:
        """Delete this transaction's own files: the stage, its upload
        temporaries and a promotion leftover. Anything else stays; its names
        are returned."""
        with contextlib.suppress(FileNotFoundError):
            os.unlink(paths["part"])
        stage_dir = paths["stage_dir"]
        st = _lstat(stage_dir)
        if st is None:
            return []
        if not stat.S_ISDIR(st.st_mode):
            raise Refused(409, "unsafe_path", f"{self._rel(stage_dir)} is not a directory")
        own = paths["stage"].name
        for entry in os.scandir(stage_dir):
            ours = entry.name == own or (entry.name.startswith(".upload-") and entry.name.endswith(".part"))
            if ours and not entry.is_dir(follow_symlinks=False):
                os.unlink(entry.path)
        left = sorted(self._rel(stage_dir / name) for name in os.listdir(stage_dir))
        if not left:
            os.rmdir(stage_dir)
            with contextlib.suppress(OSError):
                os.rmdir(paths["updates"])  # only when empty
            _fsync_dir(paths["dir"])
        return left

    def _prune_archives(self, oid: str) -> List[str]:
        """Keep the archives of the newest archive_keep finalized
        transactions. Only archives this service created are removed."""
        done = [r for r in self._records(oid)
                if r.get("state") == "finalized" and not r.get("archive_pruned") and TXN_ID.match(r.get("txn", ""))]
        done.sort(key=lambda r: r.get("promoted") or r.get("created") or "")
        pruned = []
        for record in done[:max(0, len(done) - self.config.archive_keep)]:
            path = self._paths(oid, record["txn"])["archive"]
            st = _lstat(path)
            if st is not None and stat.S_ISREG(st.st_mode):
                os.unlink(path)
                pruned.append(self._rel(path))
            record["archive_pruned"] = _now()
            self._write(record)
        return pruned

    def _dependents(self, oid: str) -> List[str]:
        """Ontologies whose import catalogue (<data_root>/import_catalogues/
        <id>.json) names this ontology's served file, as the worker sees it."""
        target = posixpath.normpath(self.worker_path(oid))
        catalogues = self.config.data_root / "import_catalogues"
        st = _lstat(catalogues)
        if st is None or not stat.S_ISDIR(st.st_mode):
            return []
        dependents = []
        for entry in sorted(os.scandir(catalogues), key=lambda e: e.name):
            if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                continue
            try:
                if entry.stat(follow_symlinks=False).st_size > 4 << 20:
                    continue
                with open(entry.path, encoding="utf-8") as f:
                    imports = json.load(f).get("imports") or {}
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(imports, dict) and any(
                    isinstance(v, str) and posixpath.normpath(v) == target for v in imports.values()):
                dependents.append(entry.name[:-len(".json")])
        return dependents


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class PrepareBody(BaseModel):
    sha256: str
    size: int
    base_sha256: str


class PromoteBody(BaseModel):
    sha256: str
    base_sha256: str


class FinalizeBody(BaseModel):
    sha256: str
    served_changed_ok: bool = False


async def _reply(send, status: int, payload: Dict[str, Any], headers=()) -> None:
    data = json.dumps(payload).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode()), *headers]})
    await send({"type": "http.response.body", "body": data})


class Gate:
    """ASGI middleware for /v1: check the bearer token from the headers
    before any body is read, and read a JSON body only up to
    JSON_BODY_LIMIT, declared or chunked. The upload route streams its own
    body, bounded by the prepared size."""

    def __init__(self, app, token: bytes, limit: int = JSON_BODY_LIMIT):
        self.app = app
        self.token = token
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope.get("headers") or []:
            headers[key.lower()] = value
        scheme, _, given = headers.get(b"authorization", b"").partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(given.strip(), self.token):
            return await _reply(send, 401, {"detail": "unauthorized"}, [(b"www-authenticate", b"Bearer")])
        if scope["method"] == "PUT" and scope["path"].endswith("/file"):
            return await self.app(scope, receive, send)
        length = headers.get(b"content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                return await _reply(send, 400, {"error": "bad_request", "message": "invalid Content-Length"})
            if declared > self.limit:
                return await _reply(send, 413, {"error": "body_too_large", "message": "the JSON body is too large"})
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            body += message.get("body", b"")
            if len(body) > self.limit:
                return await _reply(send, 413, {"error": "body_too_large", "message": "the JSON body is too large"})
            if not message.get("more_body"):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


def create_app(config: Config) -> FastAPI:
    service = TransferService(config)

    def authorize(request: Request) -> None:
        # The token is accepted in the Authorization header only.
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.strip().encode("utf-8"), config.token):
            raise HTTPException(status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"})

    def check(oid: str, txn: Optional[str] = None) -> None:
        if oid not in config.ontologies:
            raise Refused(403, "not_permitted", "this ontology is not configured for this transfer service")
        if txn is not None and not TXN_ID.match(txn):
            raise Refused(400, "bad_transaction_id", "a transaction id is lower-case letters, digits and hyphens")

    async def call(oid: str, txn: Optional[str], operation, *args) -> JSONResponse:
        try:
            check(oid, txn)
            status, body = await asyncio.to_thread(operation, *args)
        except Refused as r:
            return r.response()
        return JSONResponse(body, status_code=status)

    router = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @router.get("/ontologies/{oid}/served")
    async def served(oid: str):
        return await call(oid, None, service.served, oid)

    @router.put("/ontologies/{oid}/transactions/{txn}")
    async def prepare(oid: str, txn: str, body: PrepareBody):
        return await call(oid, txn, service.prepare, oid, txn, body.sha256, body.size, body.base_sha256)

    @router.get("/ontologies/{oid}/transactions/{txn}")
    async def status(oid: str, txn: str):
        return await call(oid, txn, service.status, oid, txn)

    @router.post("/ontologies/{oid}/transactions/{txn}/promote")
    async def promote(oid: str, txn: str, body: PromoteBody):
        return await call(oid, txn, service.promote, oid, txn, body.sha256, body.base_sha256)

    @router.post("/ontologies/{oid}/transactions/{txn}/finalize")
    async def finalize(oid: str, txn: str, body: FinalizeBody):
        return await call(oid, txn, service.finalize, oid, txn, body.sha256, body.served_changed_ok)

    @router.post("/ontologies/{oid}/transactions/{txn}/abort")
    async def abort(oid: str, txn: str):
        return await call(oid, txn, service.abort, oid, txn)

    @router.put("/ontologies/{oid}/transactions/{txn}/file")
    async def upload(oid: str, txn: str, request: Request):
        """The body is the release, exactly the declared size. It is written
        to a private temporary file, and becomes the stage only when its size
        and SHA-256 match the prepared values."""
        try:
            check(oid, txn)
            record, tmp, fd = await asyncio.to_thread(service.begin_upload, oid, txn)
        except Refused as r:
            return r.response()
        declared = record["size"]
        complete = False
        try:
            length = request.headers.get("content-length")
            if length is not None and length.strip() != str(declared):
                raise Refused(400, "length_mismatch", f"Content-Length {length} is not the declared size {declared}")
            sha = hashlib.sha256()
            received = 0
            try:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > declared:
                        raise Refused(413, "body_too_large", f"more than the declared {declared} bytes")
                    sha.update(chunk)
                    _write_all(fd, chunk)
            except ClientDisconnect:
                raise Refused(400, "truncated", f"the client left after {received} of {declared} bytes") from None
            if received != declared:
                raise Refused(400, "truncated", f"received {received} of {declared} bytes")
            if sha.hexdigest() != record["sha256"]:
                raise Refused(422, "checksum_mismatch", "the body does not have the declared SHA-256")
            await asyncio.to_thread(os.fsync, fd)
            complete = True
        except Refused as r:
            logger.warning("%s %s: upload refused: %s", oid, txn, r.message)
            return r.response()
        finally:
            os.close(fd)
            if not complete:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp)
        try:
            status, body = await asyncio.to_thread(service.install, oid, txn, tmp)
        except Refused as r:
            return r.response()
        return JSONResponse(body, status_code=status)

    app = FastAPI(title="AberOWL ontology transfer", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.include_router(router)
    # Outermost: no /v1 body is read before the token is checked.
    app.add_middleware(Gate, token=config.token)
    app.state.service = service
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn aberowl_transfer:create_app_from_env --factory"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_config())
