"""
Helpers for the update queue and runner tests: fakeredis clients that share
one server (separate connections, as separate processes would have), queue
settings, and a controllable stand-in for the update pipeline.

The queue's transitions are WATCH/MULTI/EXEC transactions, which fakeredis
implements without Lua. tests/test_update_jobs_redis.py repeats the
concurrency checks against a real Redis in separate processes.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app.intake import update_jobs, updater  # noqa: E402

fakeredis = pytest.importorskip("fakeredis")

WORKER = "http://worker-1:8080"
SECRET = "registry-secret-do-not-show"


def make_client(server):
    cls = getattr(fakeredis, "FakeAsyncRedis", None)
    if cls is None:
        from fakeredis import aioredis
        cls = aioredis.FakeRedis
    return cls(server=server, decode_responses=True)


def settings(mode="queued", **over):
    env = {"UPDATE_EXECUTION": mode, "UPDATE_QUEUE_PREFIX": "test:update_jobs",
           "UPDATE_RUNNER_HEARTBEAT_SECONDS": "1", "UPDATE_RUNNER_STALE_SECONDS": "5",
           "UPDATE_RUNNER_POLL_SECONDS": "0.1", "UPDATE_WAIT_SECONDS": "1"}
    env.update({k: str(v) for k, v in over.items()})
    s = update_jobs.load_settings(env)
    assert s.error is None, s.error
    return s


def entry(oid, **over):
    e = {"ontology": oid, "ontology_id": oid, "url": WORKER, "secret_key": SECRET, "status": "online",
         "source": "manual", "source_url": f"https://purl.obolibrary.org/obo/{oid}.owl", "update_status": "ok"}
    e.update(over)
    return e


async def seed(client, *entries):
    for e in entries:
        await client.hset(update_jobs.REGISTRY_KEY, e["ontology_id"], json.dumps(e))


async def registry(client, oid):
    raw = await client.hget(update_jobs.REGISTRY_KEY, oid)
    return json.loads(raw) if raw else None


def outcome(status="unchanged", **extra):
    result = {"success": status in ("updated", "unchanged"), "status": status, "error": None, "message": "",
              "recovery_required": False, "changed": status == "updated"}
    result.update(extra)
    return result


class FakePipeline:
    """Replaces updater.execute_update_pipeline and execute_reindex. Each
    call records its arguments and the number of calls in flight; outcomes
    are taken per ontology, and gate (an asyncio.Event) holds calls until
    set. A callable outcome is called with the registry client."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.in_flight = 0
        self.peak = 0
        self.outcomes = {}
        self.gate = None
        self.started = asyncio.Event()
        monkeypatch.setattr(updater, "execute_update_pipeline", self._update)
        monkeypatch.setattr(updater, "execute_reindex", self._reindex)

    async def _run(self, kind, kw):
        self.calls.append((kind, kw["ontology_id"], kw))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            await asyncio.sleep(0)
            result = self.outcomes.get(kw["ontology_id"], outcome())
            if callable(result):
                result = await result(kw["redis_client"])
            if isinstance(result, BaseException):
                raise result
            return result
        finally:
            self.in_flight -= 1

    async def _update(self, **kw):
        return await self._run("update", kw)

    async def _reindex(self, **kw):
        return await self._run("reindex", kw)

    def ids(self):
        return [c[1] for c in self.calls]


async def recovery_txn(client, oid, txn="txn-9"):
    """What the pipeline leaves when an outcome is unknown: the transaction
    record, recovery_required."""
    e = await registry(client, oid)
    e["update_txn"] = {"id": txn, "kind": "update", "phase": "hotswap", "state": "recovery_required",
                       "worker": WORKER, "task_id": "swap-1", "error": "hotswap_unknown: timeout"}
    await client.hset(update_jobs.REGISTRY_KEY, oid, json.dumps(e))
    return updater._outcome("failed", "hotswap_unknown", "timeout", recovery_required=True, txn=txn)


class Interleaved:
    """A Redis client whose WATCH transactions run hook() once, right after
    their first read of the hash `key`: another process writing between a
    transaction's read and its write. Everything else passes through. The
    hook must use another client."""

    def __init__(self, client, key, hook):
        self._client, self._key, self._hook = client, key, hook
        self.fired = 0

    def __getattr__(self, name):
        return getattr(self._client, name)

    def pipeline(self, *a, **k):
        return _InterleavedPipe(self, self._client.pipeline(*a, **k))


class _InterleavedPipe:
    def __init__(self, owner, inner):
        self._owner, self._inner = owner, inner

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def hget(self, name, key):
        value = await self._inner.hget(name, key)
        if name == self._owner._key and not self._owner.fired:
            self._owner.fired += 1
            await self._owner._hook()
        return value


async def write_txn(client, oid, txn="txn-runner", phase="index"):
    """What a runner's pipeline writes: a transaction record on the entry."""
    e = await registry(client, oid)
    e["update_txn"] = {"id": txn, "kind": "update", "phase": phase, "state": "running", "worker": WORKER}
    e["source_md5"] = "md5-from-runner"
    await client.hset(update_jobs.REGISTRY_KEY, oid, json.dumps(e))


async def age_heartbeat(client, s, runner_id, seconds):
    """Pretend the runner's last heartbeat is seconds old."""
    q = update_jobs.JobQueue(client, s)
    raw = await client.hget(q.k_runners, runner_id)
    beat = json.loads(raw)
    from datetime import datetime, timedelta, timezone
    beat["at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    await client.hset(q.k_runners, runner_id, json.dumps(beat))
