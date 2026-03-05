"""
Ontology update pipeline.

Orchestrates the full update process for a single ontology:
  1. HTTP version check (ETag / Last-Modified / MD5 fallback)
  2. Download to staging file
  3. Validate via OntologyServer validateOntology endpoint
  4. Load into Virtuoso staging graph (LOAD <file:///...>)
  5. Trigger ES indexing via OntologyServer triggerIndexing endpoint
  6. Trigger hot-swap on OntologyServer
  7. Promote Virtuoso staging → live graph (COPY + DROP)
  8. Promote ES staging index → alias
  9. Archive previous OWL file
 10. Update Redis metadata
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

# How long to wait for OntologyServer hot-swap to complete (seconds)
HOTSWAP_TIMEOUT = 1800
POLL_INTERVAL = 10


# ---------------------------------------------------------------------------
# Version checking
# ---------------------------------------------------------------------------

async def check_version(
    source_url: str,
    stored_etag: Optional[str],
    stored_last_modified: Optional[str],
    stored_md5: Optional[str],
    session: aiohttp.ClientSession,
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
    """
    result = {
        "changed": False,
        "etag": stored_etag,
        "last_modified": stored_last_modified,
        "need_md5": False,
        "permanent_redirect": None,
        "error": None,
    }
    try:
        async with session.head(
            source_url,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            # Track permanent redirects to update stored URL
            if resp.history:
                for r in resp.history:
                    if r.status in (301, 308):
                        result["permanent_redirect"] = str(resp.url)

            if resp.status == 404:
                result["error"] = "source_gone"
                return result
            if resp.status == 410:
                result["error"] = "source_gone"
                return result
            if resp.status >= 500:
                result["error"] = f"server_error_{resp.status}"
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

async def download_ontology(
    source_url: str,
    dest_path: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """
    Stream-download source_url to dest_path.

    Handles transparent gzip decompression. Returns {"md5": ..., "size": ...}
    on success, or {"error": ...} on failure.
    """
    import gzip as _gzip
    import io

    logger.info("Downloading %s → %s", source_url, dest_path)
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        async with session.get(
            source_url,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=3600),
        ) as resp:
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}"}

            content_encoding = resp.headers.get("Content-Encoding", "")
            content_type = resp.headers.get("Content-Type", "")
            is_gzipped = (
                content_encoding == "gzip"
                or source_url.endswith(".gz")
                or "gzip" in content_type
            )

            md5 = hashlib.md5()
            size = 0

            tmp_path = dest_path + ".tmp"
            with open(tmp_path, "wb") as fout:
                if is_gzipped:
                    buf = io.BytesIO()
                    async for chunk in resp.content.iter_chunked(65536):
                        buf.write(chunk)
                    buf.seek(0)
                    try:
                        with _gzip.GzipFile(fileobj=buf) as gz:
                            data = gz.read()
                    except Exception:
                        buf.seek(0)
                        data = buf.read()
                    fout.write(data)
                    md5.update(data)
                    size = len(data)
                else:
                    async for chunk in resp.content.iter_chunked(65536):
                        fout.write(chunk)
                        md5.update(chunk)
                        size += len(chunk)

            os.replace(tmp_path, dest_path)
            md5_hex = md5.hexdigest()
            logger.info("Downloaded %s bytes (md5=%s) to %s", size, md5_hex, dest_path)
            return {"md5": md5_hex, "size": size}

    except Exception as e:
        logger.error("Download error for %s: %s", source_url, e)
        if os.path.exists(dest_path + ".tmp"):
            os.remove(dest_path + ".tmp")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# OntologyServer API helpers
# ---------------------------------------------------------------------------

async def validate_via_server(server_url: str, owl_path_on_server: str) -> bool:
    """Ask the OntologyServer to validate an OWL file using OWLAPI."""
    url = f"{server_url.rstrip('/')}/api/validateOntology.groovy"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params={"owl_path": owl_path_on_server},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return data.get("status") == "ok"
                logger.warning("Validation failed at %s: HTTP %s", url, resp.status)
                return False
    except Exception as e:
        logger.error("Validation error for %s: %s", owl_path_on_server, e)
        return False


async def trigger_hotswap(
    server_url: str,
    secret_key: str,
    ontology_id: str,
    owl_path_on_server: str,
    callback_url: Optional[str] = None,
) -> Optional[str]:
    """
    POST to the OntologyServer to trigger an async hot-swap.
    Returns the task_id, or None on failure.
    """
    url = f"{server_url.rstrip('/')}/api/updateOntology.groovy"
    payload = {
        "secret_key": secret_key,
        "ontology": ontology_id,
        "owl_path": owl_path_on_server,
    }
    if callback_url:
        payload["callback_url"] = callback_url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 202:
                    data = await resp.json(content_type=None)
                    return data.get("task_id")
                body = await resp.text()
                logger.error("Hot-swap trigger failed (%s): %s", resp.status, body[:200])
                return None
    except Exception as e:
        logger.error("Hot-swap trigger error: %s", e)
        return None


async def wait_for_hotswap(
    server_url: str, task_id: str, timeout_secs: int = HOTSWAP_TIMEOUT
) -> bool:
    """Poll the OntologyServer until the hot-swap succeeds, fails, or times out."""
    url = f"{server_url.rstrip('/')}/api/updateStatus.groovy"
    elapsed = 0
    async with aiohttp.ClientSession() as session:
        while elapsed < timeout_secs:
            try:
                async with session.get(
                    url,
                    params={"task_id": task_id},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        status = data.get("status")
                        if status == "success":
                            logger.info("Hot-swap %s completed successfully", task_id)
                            return True
                        if status == "failed":
                            logger.error(
                                "Hot-swap %s failed: %s",
                                task_id, data.get("message"),
                            )
                            return False
                        # "pending" or "running" — keep polling
            except Exception as e:
                logger.warning("Poll error for task %s: %s", task_id, e)

            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

    logger.error("Hot-swap %s timed out after %ss", task_id, timeout_secs)
    return False


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
    """
    POST to the OntologyServer to trigger async ES indexing.
    Returns task_id or None.
    """
    url = f"{server_url.rstrip('/')}/api/triggerIndexing.groovy"
    payload = {
        "secret_key": secret_key,
        "ontology": ontology_id,
        "owl_path": owl_path_on_server,
        "es_url": es_url,
        "ontology_index": ontology_index,
        "class_index": class_index,
        "ontology_name": ontology_name,
        "description": description,
        "fresh_index": "True",
    }
    if callback_url:
        payload["callback_url"] = callback_url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 202:
                    data = await resp.json(content_type=None)
                    return data.get("task_id")
                body = await resp.text()
                logger.error("Indexing trigger failed (%s): %s", resp.status, body[:200])
                return None
    except Exception as e:
        logger.error("Indexing trigger error: %s", e)
        return None


async def wait_for_task(
    server_url: str, task_id: str, timeout_secs: int = HOTSWAP_TIMEOUT
) -> bool:
    """Poll updateStatus for any generic task (indexing, hotswap)."""
    return await wait_for_hotswap(server_url, task_id, timeout_secs)


# ---------------------------------------------------------------------------
# Full update pipeline
# ---------------------------------------------------------------------------

async def execute_update_pipeline(
    ontology_id: str,
    registry_entry: Dict[str, Any],
    redis_client,
    virtuoso_mgr,
    es_mgr,
    ontologies_base_path: str,
    es_url: str,
) -> Dict[str, Any]:
    """
    Execute the full update pipeline for one ontology.

    Returns {"success": bool, "error": str|None, "new_md5": str|None}
    """
    source_url = registry_entry.get("source_url")
    server_url = registry_entry.get("server_url")
    secret_key = registry_entry.get("secret_key", "")
    stored_etag = registry_entry.get("source_etag")
    stored_lm = registry_entry.get("source_last_modified")
    stored_md5 = registry_entry.get("source_md5")
    name = registry_entry.get("name", ontology_id)
    description = registry_entry.get("description", "")

    if not source_url:
        return {"success": False, "error": "no_source_url"}

    ont_dir = Path(ontologies_base_path) / ontology_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    staging_path_host = str(ont_dir / f"{ontology_id}_staging.owl")
    # Path as seen inside each container (mounted at /data/ontologies/{id}/ → /data/)
    staging_path_container = f"/data/{ontology_id}_staging.owl"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    async with aiohttp.ClientSession() as session:
        # Step 1: Version check
        version_info = await check_version(
            source_url, stored_etag, stored_lm, stored_md5, session
        )

        if version_info.get("error") == "source_gone":
            await _update_registry_status(
                redis_client, ontology_id, "source_gone", "Source URL returned 404/410"
            )
            return {"success": False, "error": "source_gone"}

        if version_info.get("error"):
            await _update_registry_status(
                redis_client, ontology_id, "check_failed", version_info["error"]
            )
            return {"success": False, "error": version_info["error"]}

        if version_info.get("permanent_redirect"):
            logger.info(
                "Permanent redirect for %s: updating source_resolved_url to %s",
                ontology_id, version_info["permanent_redirect"],
            )
            registry_entry["source_resolved_url"] = version_info["permanent_redirect"]

        # Step 2: Download
        dl_result = await download_ontology(source_url, staging_path_host, session)
        if "error" in dl_result:
            await _update_registry_status(
                redis_client, ontology_id, "download_failed", dl_result["error"]
            )
            return {"success": False, "error": f"download: {dl_result['error']}"}

        new_md5 = dl_result["md5"]

        # MD5 fallback: if no version headers were present, check MD5 now
        if version_info.get("need_md5") and new_md5 == stored_md5:
            logger.info("%s: MD5 unchanged (%s), no update needed", ontology_id, new_md5)
            await _touch_last_checked(redis_client, ontology_id)
            _cleanup_staging(staging_path_host)
            return {"success": True, "error": None, "new_md5": new_md5, "changed": False}

    # Step 3: Validate via OntologyServer (if server is online)
    if server_url:
        valid = await validate_via_server(server_url, staging_path_container)
        if not valid:
            await _update_registry_status(
                redis_client, ontology_id, "validation_failed", "OWL parse error"
            )
            _cleanup_staging(staging_path_host)
            return {"success": False, "error": "owl_validation_failed"}

    # Step 4: Virtuoso staging load
    staging_path_virtuoso = f"/data/ontologies/{ontology_id}/{ontology_id}_staging.owl"
    ok = await virtuoso_mgr.load_to_staging(ontology_id, staging_path_virtuoso)
    if not ok:
        await _update_registry_status(
            redis_client, ontology_id, "virtuoso_load_failed", "SPARQL LOAD failed"
        )
        _cleanup_staging(staging_path_host)
        return {"success": False, "error": "virtuoso_staging_failed"}

    # Step 5: ES staging index
    new_es_index = await es_mgr.get_next_index_name(ontology_id)
    old_es_index = await es_mgr.get_current_index(ontology_id)

    es_ok = True
    if server_url:
        index_task_id = await trigger_indexing(
            server_url=server_url,
            secret_key=secret_key,
            ontology_id=ontology_id,
            owl_path_on_server=staging_path_container,
            es_url=es_url,
            ontology_index="aberowl_ontologies",
            class_index=new_es_index,
            ontology_name=name,
            description=description,
        )
        if index_task_id:
            es_ok = await wait_for_task(server_url, index_task_id, timeout_secs=1800)
        else:
            es_ok = False

    if not es_ok:
        await virtuoso_mgr.drop_staging(ontology_id)
        await es_mgr.delete_index(new_es_index)
        await _update_registry_status(
            redis_client, ontology_id, "indexing_failed", "ES indexing failed"
        )
        _cleanup_staging(staging_path_host)
        return {"success": False, "error": "es_indexing_failed"}

    # Step 6: Hot-swap OntologyServer
    if server_url:
        task_id = await trigger_hotswap(
            server_url=server_url,
            secret_key=secret_key,
            ontology_id=ontology_id,
            owl_path_on_server=staging_path_container,
        )
        if not task_id:
            await virtuoso_mgr.drop_staging(ontology_id)
            await es_mgr.delete_index(new_es_index)
            await _update_registry_status(
                redis_client, ontology_id, "hotswap_failed", "Could not start hot-swap"
            )
            _cleanup_staging(staging_path_host)
            return {"success": False, "error": "hotswap_trigger_failed"}

        swap_ok = await wait_for_hotswap(server_url, task_id)
        if not swap_ok:
            await virtuoso_mgr.drop_staging(ontology_id)
            await es_mgr.delete_index(new_es_index)
            await _update_registry_status(
                redis_client, ontology_id, "hotswap_failed", "Hot-swap did not succeed"
            )
            _cleanup_staging(staging_path_host)
            return {"success": False, "error": "hotswap_failed"}

    # Step 7 & 8: Promote Virtuoso graph and ES alias
    virt_ok = await virtuoso_mgr.promote_staging(ontology_id)
    if not virt_ok:
        logger.error("Virtuoso graph promotion failed for %s — live and staging may be inconsistent", ontology_id)

    if server_url:
        alias_ok = await es_mgr.swap_alias(ontology_id, new_es_index, old_es_index)
        if alias_ok and old_es_index:
            await es_mgr.delete_index(old_es_index)

    # Step 9: Archive old OWL file and activate new one
    active_path = str(ont_dir / f"{ontology_id}_active.owl")
    archive_path = str(ont_dir / f"{ontology_id}_{timestamp}.owl")

    if os.path.exists(active_path):
        shutil.copy2(active_path, archive_path)
        logger.info("Archived previous OWL to %s", archive_path)

    os.replace(staging_path_host, active_path)

    # Step 10: Update Redis registry
    now_iso = datetime.now(timezone.utc).isoformat()
    registry_entry.update(
        {
            "source_etag": version_info.get("etag"),
            "source_last_modified": version_info.get("last_modified"),
            "source_md5": new_md5,
            "last_checked": now_iso,
            "last_updated": now_iso,
            "update_status": "ok",
            "update_error": None,
            "active_owl_path": active_path,
            "active_es_index": new_es_index if server_url else old_es_index,
        }
    )

    # Append to update history (keep last 10)
    history = registry_entry.get("update_history", [])
    history.append(
        {"timestamp": now_iso, "md5": new_md5, "status": "success"}
    )
    registry_entry["update_history"] = history[-10:]

    from central_server.app.main import redis_client as _rc  # noqa: avoid circular at top

    await redis_client.hset(
        "ontology_registry", ontology_id, json.dumps(registry_entry)
    )
    logger.info("Update pipeline complete for %s", ontology_id)
    return {"success": True, "error": None, "new_md5": new_md5, "changed": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cleanup_staging(staging_path: str) -> None:
    try:
        if os.path.exists(staging_path):
            os.remove(staging_path)
    except Exception as e:
        logger.warning("Could not remove staging file %s: %s", staging_path, e)


async def _update_registry_status(
    redis_client, ontology_id: str, update_status: str, error_msg: str
) -> None:
    raw = await redis_client.hget("ontology_registry", ontology_id)
    if raw:
        entry = json.loads(raw)
    else:
        entry = {"ontology_id": ontology_id}
    entry["update_status"] = update_status
    entry["update_error"] = error_msg
    entry["last_checked"] = datetime.now(timezone.utc).isoformat()

    history = entry.get("update_history", [])
    history.append(
        {
            "timestamp": entry["last_checked"],
            "status": update_status,
            "error": error_msg,
        }
    )
    entry["update_history"] = history[-10:]
    await redis_client.hset("ontology_registry", ontology_id, json.dumps(entry))


async def _touch_last_checked(redis_client, ontology_id: str) -> None:
    raw = await redis_client.hget("ontology_registry", ontology_id)
    if raw:
        entry = json.loads(raw)
        entry["last_checked"] = datetime.now(timezone.utc).isoformat()
        await redis_client.hset("ontology_registry", ontology_id, json.dumps(entry))
