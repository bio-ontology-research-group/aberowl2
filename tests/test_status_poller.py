"""Tests for worker status polling.

Regression cover for the production incident on 2026-07-20: the sweep polled
every registered ontology at once, which exhausted the Redis connection pool and
raised MaxConnectionsError out of `periodic_metadata_fetch_task`. Because the
`while True` loop had no exception guard, the task died permanently and every
ontology's status froze at its last polled value for two weeks.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))


class FakeRedis:
    """Async Redis mock covering only what the poller uses."""

    def __init__(self, entries=None):
        self._data = {"registered_servers": dict(entries or {})}

    async def hgetall(self, name):
        return dict(self._data.get(name, {}))

    async def hset(self, name, key, value):
        self._data.setdefault(name, {})[key] = value

    async def hget(self, name, key):
        return self._data.get(name, {}).get(key)


def _entries(n, prefix="ont"):
    return {
        f"{prefix}{i}": json.dumps(
            {"ontology": f"{prefix}{i}", "ontology_id": f"{prefix}{i}",
             "url": f"http://worker-{i}:8080/", "status": "offline"}
        )
        for i in range(n)
    }


@pytest.fixture
def main_module():
    import app.main as m
    return m


def test_sweep_respects_concurrency_limit(main_module):
    """The sweep must never exceed STATUS_POLL_CONCURRENCY in flight.

    This is the property whose absence exhausted the Redis pool in production.
    """
    fake = FakeRedis(_entries(50))
    in_flight = 0
    peak = 0

    async def fake_fetch(server, session=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    with patch.object(main_module, "redis_client", fake), \
         patch.object(main_module, "STATUS_POLL_CONCURRENCY", 5), \
         patch.object(main_module, "fetch_and_update_server_metadata", fake_fetch), \
         patch.object(main_module, "_write_servers_to_file", AsyncMock()):
        count = asyncio.run(main_module._fetch_and_update_all_servers())

    assert count == 50
    assert peak <= 5, f"peak concurrency {peak} exceeded the limit of 5"


def test_sweep_can_refresh_a_subset(main_module):
    """`only` refreshes just the named ontologies — the deploy hook."""
    fake = FakeRedis(_entries(20))
    polled = []

    async def fake_fetch(server, session=None):
        polled.append(server["ontology"])

    with patch.object(main_module, "redis_client", fake), \
         patch.object(main_module, "fetch_and_update_server_metadata", fake_fetch), \
         patch.object(main_module, "_write_servers_to_file", AsyncMock()):
        count = asyncio.run(
            main_module._fetch_and_update_all_servers(["ont3", "ont7"])
        )

    assert count == 2
    assert sorted(polled) == ["ont3", "ont7"]


def test_one_failing_worker_does_not_abort_the_sweep(main_module):
    """A single bad worker must not prevent the others being refreshed."""
    fake = FakeRedis(_entries(10))
    polled = []

    async def fake_fetch(server, session=None):
        if server["ontology"] == "ont4":
            raise RuntimeError("worker exploded")
        polled.append(server["ontology"])

    with patch.object(main_module, "redis_client", fake), \
         patch.object(main_module, "fetch_and_update_server_metadata", fake_fetch), \
         patch.object(main_module, "_write_servers_to_file", AsyncMock()):
        count = asyncio.run(main_module._fetch_and_update_all_servers())

    assert count == 10
    assert len(polled) == 9 and "ont4" not in polled


def test_periodic_task_survives_a_failing_sweep(main_module):
    """The loop must keep running after a sweep raises.

    Previously the exception escaped and killed the task for good.
    """
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("MaxConnectionsError-alike")

    async def drive():
        with patch.object(main_module, "STATUS_POLL_INTERVAL", 0.01), \
             patch.object(main_module, "_fetch_and_update_all_servers", flaky):
            task = asyncio.create_task(main_module.periodic_metadata_fetch_task())
            await asyncio.sleep(0.15)
            still_running = not task.done()
            task.cancel()
            return still_running

    still_running = asyncio.run(drive())
    assert len(calls) >= 2, "loop stopped after the first failure"
    assert still_running, "task died instead of continuing"


def test_polling_can_be_disabled(main_module):
    """STATUS_POLL_INTERVAL=0 disables the sweep entirely."""
    called = []

    async def should_not_run():
        called.append(1)

    async def drive():
        with patch.object(main_module, "STATUS_POLL_INTERVAL", 0), \
             patch.object(main_module, "_fetch_and_update_all_servers", should_not_run):
            await asyncio.wait_for(main_module.periodic_metadata_fetch_task(), timeout=1)

    asyncio.run(drive())
    assert called == []
