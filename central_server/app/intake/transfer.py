"""
Remote transport for ontology updates.

By default the update pipeline assumes central and the worker see one file
system: central downloads into the worker's /data tree and promotes there.
When central and a worker are on different hosts, the worker's host runs the
transfer service (transfer_service/), and this module lets the pipeline use
it. Nothing changes unless UPDATE_TRANSFER_CONFIG names a mapping file:

  {"transfers": [
     {"worker_url": "http://aberowl-worker-1:8080",
      "transfer_url": "https://worker-host.internal.example:8443",
      "token_file": "/run/secrets/aberowl-transfer-host1",
      "ca_file": "/run/secrets/aberowl-transfer-ca.pem"}
  ]}

A worker URL listed there is updated through its transfer service; any other
worker keeps the shared-directory behaviour. If the variable is set but the
file is unusable, every update is refused (transfer_config_invalid) rather
than falling back. transfer_url must be https unless the entry also sets
"allow_insecure_http": true, for a private network the operator trusts. The
token comes from token_file or the environment variable named by token_env;
it is sent only in the Authorization header and never stored in the registry.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote, urlsplit

import aiohttp

logger = logging.getLogger(__name__)

CONFIG_ENV = "UPDATE_TRANSFER_CONFIG"
ENTRY_KEYS = {"worker_url", "transfer_url", "token_file", "token_env", "ca_file", "allow_insecure_http"}
MIN_TOKEN_LENGTH = 32
# Each step is tried this often when the service gives no answer; between
# attempts the service's own record decides what happens next.
ATTEMPTS = 3
RETRY_DELAY = 2.0
# Promotion hashes the stage and the served file on the worker host.
REQUEST_TIMEOUT = 600
UPLOAD_READ_TIMEOUT = 600
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TransferConfigError(Exception):
    """UPDATE_TRANSFER_CONFIG is set but cannot be used."""


@dataclass(frozen=True)
class TransferTarget:
    worker_url: str
    transfer_url: str
    token: str = field(repr=False)
    ca_file: Optional[str] = None


def normal_url(value: Any) -> Optional[str]:
    """scheme://host[:port][/path], lower-case scheme and host, no trailing
    slash; None when value is not an absolute http(s) URL without
    credentials, query or fragment."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme.lower()}://{netloc}{parts.path.rstrip('/')}"


def _token(entry: Dict[str, Any], env: Mapping[str, str], where: str) -> str:
    if ("token_file" in entry) == ("token_env" in entry):
        raise TransferConfigError(f"{where}: give exactly one of token_file and token_env")
    if "token_file" in entry:
        try:
            token = Path(entry["token_file"]).read_text(encoding="utf-8").strip()
        except (OSError, TypeError, UnicodeDecodeError) as e:
            raise TransferConfigError(f"{where}: cannot read token_file: {getattr(e, 'strerror', None) or type(e).__name__}") from None
    else:
        token = (env.get(entry["token_env"]) or "").strip() if isinstance(entry["token_env"], str) else ""
    if len(token) < MIN_TOKEN_LENGTH or any(c.isspace() for c in token) or not token.isprintable():
        raise TransferConfigError(f"{where}: the token must be at least {MIN_TOKEN_LENGTH} printable characters")
    return token


def load_targets(env: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, TransferTarget]]:
    """The configured worker -> transfer service mappings, keyed by the
    normalised worker URL; None when UPDATE_TRANSFER_CONFIG is not set."""
    env = os.environ if env is None else env
    path = (env.get(CONFIG_ENV) or "").strip()
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise TransferConfigError(f"cannot read {CONFIG_ENV} ({path}): {e}") from None
    if not isinstance(raw, dict) or set(raw) != {"transfers"} or not isinstance(raw["transfers"], list) \
            or not raw["transfers"]:
        raise TransferConfigError(f"{path} must be an object with one key, \"transfers\", a non-empty list")
    targets: Dict[str, TransferTarget] = {}
    for n, entry in enumerate(raw["transfers"]):
        where = f"{path}: transfers[{n}]"
        if not isinstance(entry, dict):
            raise TransferConfigError(f"{where} is not an object")
        unknown = sorted(set(entry) - ENTRY_KEYS)
        if unknown:
            raise TransferConfigError(f"{where}: unknown keys {', '.join(unknown)}")
        worker = normal_url(entry.get("worker_url"))
        transfer = normal_url(entry.get("transfer_url"))
        if not worker or not transfer:
            raise TransferConfigError(f"{where}: worker_url and transfer_url must be absolute http(s) URLs "
                                      "without credentials, query or fragment")
        insecure = entry.get("allow_insecure_http", False)
        if not isinstance(insecure, bool):
            raise TransferConfigError(f"{where}: allow_insecure_http must be true or false")
        if transfer.startswith("http://") and not insecure:
            raise TransferConfigError(f"{where}: transfer_url must be https, or set allow_insecure_http "
                                      "for a private network")
        ca_file = entry.get("ca_file")
        if ca_file is not None and (not isinstance(ca_file, str) or not os.path.isfile(ca_file)):
            raise TransferConfigError(f"{where}: ca_file is not a readable file")
        if worker in targets:
            raise TransferConfigError(f"{where}: {worker} is mapped twice")
        targets[worker] = TransferTarget(worker, transfer, _token(entry, env, where), ca_file)
    return targets


def target_for(server_url: str, env: Optional[Mapping[str, str]] = None) -> Optional[TransferTarget]:
    """The transfer service for a worker, None for the shared-directory
    default. Raises TransferConfigError when the configuration is unusable."""
    targets = load_targets(env)
    if targets is None:
        return None
    return targets.get(normal_url(server_url) or "")


def sha256_file(path: str) -> Tuple[str, int]:
    sha = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
            size += len(chunk)
    return sha.hexdigest(), size


class NoAnswer(Exception):
    """No HTTP answer was read. The request may have reached the service."""


def _definite(status: int) -> bool:
    """The service answered that it did not do what was asked: a client
    error other than a timeout or rate limit, or 507 (no space). Anything
    else (5xx, 408, 429) may be transient."""
    return status == 507 or (400 <= status < 500 and status not in (408, 429))


def _describe(status: int, body: Dict[str, Any]) -> str:
    detail = body.get("message") or body.get("detail") or ""
    error = body.get("error")
    return f"HTTP {status}" + (f" {error}" if error else "") + (f": {detail}" if detail else "")


class RemoteTransfer:
    """One ontology's files on one worker host, through its transfer
    service. Every method answers with a state; none raises for a network
    or service failure."""

    def __init__(self, target: TransferTarget, ontology_id: str):
        self.target = target
        self.ontology_id = ontology_id
        self._base = f"{target.transfer_url}/v1/ontologies/{quote(ontology_id, safe='')}"

    def _ssl(self) -> ssl.SSLContext:
        # An explicit verifying context: older aiohttp releases read
        # ssl=True as "do not verify".
        return ssl.create_default_context(cafile=self.target.ca_file or None)

    async def _request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None,
                       data: Any = None, headers: Optional[Dict[str, str]] = None,
                       timeout: Optional[aiohttp.ClientTimeout] = None) -> Tuple[int, Dict[str, Any]]:
        all_headers = {"Authorization": f"Bearer {self.target.token}"}
        all_headers.update(headers or {})
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, self._base + path, json=json_body, data=data, headers=all_headers,
                    timeout=timeout or aiohttp.ClientTimeout(total=REQUEST_TIMEOUT), ssl=self._ssl(),
                ) as resp:
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:
                        body = None
                    return resp.status, body if isinstance(body, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise NoAnswer(f"{method} {path or '/'}: {str(e) or type(e).__name__}") from None

    # -- reads ------------------------------------------------------------

    async def served(self) -> Dict[str, Any]:
        """The served file on the worker host: {"state": "ok", "sha256",
        "md5", "size", "worker_path", "catalogue_dependents", ...},
        {"state": "missing"}, {"state": "refused"} or {"state": "unreachable"}."""
        try:
            status, body = await self._request("GET", "/served")
        except NoAnswer as e:
            return {"state": "unreachable", "message": str(e)}
        if status == 200 and _SHA256.match(str(body.get("sha256"))) and body.get("md5"):
            return {**body, "state": "ok"}
        if status == 404 and body.get("error") == "served_missing":
            return {"state": "missing", "message": _describe(status, body)}
        if status >= 500:
            return {"state": "unreachable", "message": _describe(status, body)}
        return {"state": "refused", "message": _describe(status, body)}

    async def status(self, txn: str) -> Dict[str, Any]:
        """The service's record of txn and the files as they are:
        {"state": "ok", "observed", ...}, {"state": "absent"} (no record)
        or {"state": "unreachable"}."""
        try:
            status, body = await self._request("GET", f"/transactions/{txn}")
        except NoAnswer as e:
            return {"state": "unreachable", "message": str(e)}
        if status == 200 and body.get("observed"):
            return {**body, "state": "ok", "record_state": body.get("state")}
        if status == 404 and body.get("error") == "unknown_transaction":
            return {"state": "absent", "message": _describe(status, body)}
        return {"state": "unreachable", "message": _describe(status, body)}

    # -- stage ------------------------------------------------------------

    async def stage(self, txn: str, local_path: str, sha256: str, size: int, base_sha256: str) -> Dict[str, Any]:
        """Prepare txn (compare and set on the served file's SHA-256) and
        upload the release. Returns {"state": "staged"}, {"state": "failed",
        "message"} when the service answered that it holds no stage (abort
        then removes its record and directory), or {"state": "unknown",
        "message"} when what it holds could not be established."""
        last = ""
        for attempt in range(ATTEMPTS):
            if attempt:
                await asyncio.sleep(RETRY_DELAY * attempt)
            outcome = await self._stage_once(txn, local_path, sha256, size, base_sha256)
            if outcome["state"] in ("staged", "failed"):
                return outcome
            last = outcome["message"]
            logger.warning("%s %s: transfer attempt %d: %s", self.ontology_id, txn, attempt + 1, last)
        current = await self.status(txn)
        if current["state"] == "absent":
            return {"state": "failed", "message": last}
        if current["state"] == "ok":
            if self._staged(current, sha256):
                return {"state": "staged"}
            if current.get("observed") == "prepared":
                return {"state": "failed", "message": last}
        return {"state": "unknown", "message": f"{last}; transfer status: "
                                               f"{current.get('observed') or current.get('message')}"}

    @staticmethod
    def _staged(current: Dict[str, Any], sha256: str) -> bool:
        """Staged by the service's journal, and the stage it sees is the
        release. A complete stage file beside a journal that still says
        "prepared" is not enough: the service records it as staged first."""
        stage = current.get("stage_file") or {}
        return (current.get("record_state") == "staged" and current.get("observed") == "staged"
                and stage.get("sha256") == sha256)

    async def _stage_once(self, txn, local_path, sha256, size, base_sha256) -> Dict[str, Any]:
        try:
            status, body = await self._request("PUT", f"/transactions/{txn}", json_body={
                "sha256": sha256, "size": size, "base_sha256": base_sha256})
        except NoAnswer as e:
            return {"state": "retry", "message": f"prepare: {e}"}
        if not _definite(status) and status >= 400:
            return {"state": "retry", "message": f"prepare: {_describe(status, body)}"}
        if status not in (200, 201):
            # Not prepared: the served file changed, the ontology is not
            # permitted, the release is too large, the disk is too full, or
            # another transaction is open there.
            return {"state": "failed", "message": f"prepare: {_describe(status, body)}"}
        if body.get("state") != "prepared":
            # Prepared and uploaded before, by an attempt whose answer was lost.
            current = await self.status(txn)
            if current["state"] == "ok" and self._staged(current, sha256):
                return {"state": "staged"}
            return {"state": "retry", "message": f"transaction is {body.get('state')}"}
        try:
            with open(local_path, "rb") as f:
                status, body = await self._request(
                    "PUT", f"/transactions/{txn}/file", data=f,
                    headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=UPLOAD_READ_TIMEOUT))
        except NoAnswer as e:
            return {"state": "retry", "message": f"upload: {e}"}
        except OSError as e:
            return {"state": "failed", "message": f"cannot read the download: {e}"}
        if status in (200, 201):
            current = await self.status(txn)
            if current["state"] == "ok" and self._staged(current, sha256):
                return {"state": "staged"}
            return {"state": "retry", "message": f"not staged after the upload: {current.get('observed') or current.get('message')}"}
        if status in (400, 422) or not _definite(status):
            # Truncated or corrupted on the way, or a service error: the
            # temporary file was discarded; the next attempt sends it again.
            return {"state": "retry", "message": f"upload: {_describe(status, body)}"}
        return {"state": "failed", "message": f"upload: {_describe(status, body)}"}

    # -- promote ----------------------------------------------------------

    async def promote(self, txn: str, sha256: str, base_sha256: str) -> Dict[str, Any]:
        """Archive and replace the served file on the worker host, then read
        it back. Returns {"state": "promoted", "archive", "served",
        "catalogue_dependents"} only when the served file is seen to hold
        sha256; {"state": "refused", "message"} when the service answered
        that it did not promote; {"state": "unknown", "message"} otherwise."""
        last = ""
        for attempt in range(ATTEMPTS):
            if attempt:
                await asyncio.sleep(RETRY_DELAY * attempt)
            try:
                status, body = await self._request("POST", f"/transactions/{txn}/promote", json_body={
                    "sha256": sha256, "base_sha256": base_sha256})
                if status == 200:
                    return await self._verify_promoted(sha256, body)
                if _definite(status):
                    # Including 507: no space to copy where links are not
                    # allowed. The served file was not replaced.
                    return {"state": "refused", "message": _describe(status, body)}
                last = _describe(status, body)
            except NoAnswer as e:
                last = str(e)
            # No answer, or a service error: the files say what happened.
            current = await self.status(txn)
            if current["state"] == "ok" and current.get("observed") == "promoted":
                return await self._verify_promoted(sha256, current)
            if current["state"] == "ok" and current.get("observed") == "staged":
                continue  # not replaced yet: the compare and set makes a repeat safe
            return {"state": "unknown", "message": f"promote: {last}; transfer status: "
                                                   f"{current.get('observed') or current.get('message')}"}
        return {"state": "unknown", "message": f"promote: {last}"}

    async def _verify_promoted(self, sha256: str, body: Dict[str, Any]) -> Dict[str, Any]:
        served = await self.served()
        if served["state"] != "ok":
            return {"state": "unknown", "message": f"the served file could not be read back: {served.get('message')}"}
        if served["sha256"] != sha256:
            return {"state": "refused", "message": f"the served file holds {served['sha256']}, not {sha256}"}
        return {"state": "promoted", "archive": body.get("archive"), "served": served,
                "catalogue_dependents": served.get("catalogue_dependents")}

    # -- cleanup ----------------------------------------------------------

    async def release(self, txn: str, sha256: str) -> Dict[str, Any]:
        """Remove the stage of a promoted transaction (the worker no longer
        reads it). Returns {"ok": bool, "message"}."""
        try:
            status, body = await self._request("POST", f"/transactions/{txn}/finalize", json_body={"sha256": sha256})
        except NoAnswer as e:
            return {"ok": False, "message": str(e)}
        return {"ok": status == 200, "message": _describe(status, body), "left_in_place": body.get("left_in_place")}

    async def abort(self, txn: str) -> Dict[str, Any]:
        """Remove the stage of a transaction that was never promoted. A
        transaction the service never recorded counts as removed."""
        try:
            status, body = await self._request("POST", f"/transactions/{txn}/abort")
        except NoAnswer as e:
            return {"ok": False, "message": str(e)}
        if status == 404 and body.get("error") == "unknown_transaction":
            return {"ok": True, "message": "nothing was recorded"}
        return {"ok": status == 200, "message": _describe(status, body)}
