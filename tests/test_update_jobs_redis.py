"""
The update queue against a real Redis, across separate processes: one job
at a time among many runners, every queued job run exactly once, inline runs
and runner claims excluding each other, and a released claim refused to a
returning runner.

Skipped unless ABEROWL_TEST_REDIS_URL names a Redis that holds nothing of
value, such as a throwaway container on loopback. The tests use their own
key prefix and delete only those keys; the queue also reads (never writes)
the registry hash, registered_servers. Never point this at a deployment's
Redis.

    docker run --rm -d --name aberowl-queue-test -p 127.0.0.1:16379:6379 redis:7-alpine
    ABEROWL_TEST_REDIS_URL=redis://127.0.0.1:16379/0 \\
      uv run --extra test python -m pytest tests/test_update_jobs_redis.py -v
    docker rm -f aberowl-queue-test
"""

import asyncio
import json
import multiprocessing
import os
import secrets
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

import redis.asyncio as redis  # noqa: E402

from app.intake import update_jobs  # noqa: E402

URL = os.getenv("ABEROWL_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set ABEROWL_TEST_REDIS_URL to a throwaway Redis")


def _settings(prefix):
    return update_jobs.load_settings({"UPDATE_EXECUTION": "queued", "UPDATE_QUEUE_PREFIX": prefix,
                                      "UPDATE_RUNNER_HEARTBEAT_SECONDS": "1", "UPDATE_RUNNER_STALE_SECONDS": "5",
                                      "UPDATE_QUEUE_MAX": "1000"})


async def _ok(marker):
    return {"status": "unchanged"}, None


async def _loop(url, prefix, name, rounds, inline):
    client = redis.from_url(url, decode_responses=True)
    q = update_jobs.JobQueue(client, _settings(prefix))
    log = f"{prefix}:test:log"
    done = idle = 0
    try:
        while done < rounds and idle < 1000:
            if inline:
                gate = update_jobs.InlineGate(name)
                try:
                    marker = await gate.begin(q, gate.reserve(), "update", ["x"])
                except update_jobs.QueueError:
                    idle += 1
                    await asyncio.sleep(0.003)
                    continue
                label = marker["job"]
            else:
                job, _ = await q.claim(name)
                if job is None:
                    idle += 1
                    await asyncio.sleep(0.003)
                    continue
                label = job["id"]
            await client.rpush(log, json.dumps(["start", name, label]))
            await asyncio.sleep(0.01)
            await client.rpush(log, json.dumps(["end", name, label]))
            if inline:
                await gate.run(q, marker, _ok)
            else:
                await q.finish(job["id"], job["claim"], hold=False)
            done += 1
    finally:
        await client.aclose()


def _process(url, prefix, name, rounds, inline):
    asyncio.run(_loop(url, prefix, name, rounds, inline))


@pytest.fixture
async def prefix():
    p = f"aberowl-test:{secrets.token_hex(4)}"
    yield p
    client = redis.from_url(URL, decode_responses=True)
    keys = [k async for k in client.scan_iter(match=f"{p}:*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


def _spawn(prefix, specs):
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_process, args=(URL, prefix, name, rounds, inline))
             for name, rounds, inline in specs]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]


async def _log(prefix):
    client = redis.from_url(URL, decode_responses=True)
    try:
        return [json.loads(x) for x in await client.lrange(f"{prefix}:test:log", 0, -1)]
    finally:
        await client.aclose()


def _assert_one_at_a_time(log):
    assert len(log) % 2 == 0
    for start, end in zip(log[::2], log[1::2]):
        assert start[0] == "start" and end[0] == "end" and start[1:] == end[1:], (start, end)


async def test_many_runner_processes_run_each_job_once(prefix):
    client = redis.from_url(URL, decode_responses=True)
    q = update_jobs.JobQueue(client, _settings(prefix))
    ids = [(await q.enqueue("update", [f"o{i}"]))[0]["id"] for i in range(30)]
    await client.aclose()
    _spawn(prefix, [(f"runner-{i}", 30, False) for i in range(6)])
    log = await _log(prefix)
    _assert_one_at_a_time(log)
    assert sorted(entry[2] for entry in log[::2]) == sorted(ids)


async def test_inline_and_runner_processes_exclude_each_other(prefix):
    client = redis.from_url(URL, decode_responses=True)
    q = update_jobs.JobQueue(client, _settings(prefix))
    for i in range(20):
        await q.enqueue("update", [f"o{i}"])
    await client.aclose()
    # One inline process: inline mode supports a single API process, and two
    # would not exclude each other.
    _spawn(prefix, [("runner-a", 20, False), ("runner-b", 20, False), ("api-a", 10, True)])
    log = await _log(prefix)
    _assert_one_at_a_time(log)
    assert sum(1 for e in log[::2] if e[1].startswith("runner")) == 20


async def test_released_claim_is_refused_to_a_returning_runner(prefix):
    s = _settings(prefix)
    a = update_jobs.JobQueue(redis.from_url(URL, decode_responses=True), s)
    b = update_jobs.JobQueue(redis.from_url(URL, decode_responses=True), s)
    try:
        first, _ = await a.enqueue("update", ["o1"])
        await a.enqueue("update", ["o2"])
        claimed, _ = await a.claim("runner-a")
        await a.r.hset(a.k_runners, "runner-a", json.dumps({"at": "2000-01-01T00:00:00+00:00"}))
        released = await b.recover(first["id"], claimed["claim"], "runner-a stopped", reconciled=True,
                                   runner_stopped=True)
        assert released["status"] == "released"
        nxt, _ = await b.claim("runner-b")
        assert nxt["ontologies"] == ["o2"]
        with pytest.raises(update_jobs.OwnershipLost):
            await a.finish(first["id"], claimed["claim"], hold=False)
        assert (await b.finish(nxt["id"], nxt["claim"], hold=False))["state"] == "done"
        assert (await b.get(first["id"]))["state"] == "recovered"
    finally:
        await a.r.aclose()
        await b.r.aclose()
