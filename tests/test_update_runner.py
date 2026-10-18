"""
The update runner (central_server/app/update_runner.py): it runs queued jobs
through the update pipeline one at a time, records each outcome, keeps the
global slot through unknown outcomes and interruptions, and stops when its
claim is gone. The pipeline itself is replaced by a controllable fake
(update_job_helpers.FakePipeline); tests/test_update_pipeline.py covers it.
"""

import asyncio
import json

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from tests.update_job_helpers import (FakePipeline, age_heartbeat, entry, fakeredis, make_client, outcome,
                                      recovery_txn, registry, seed, settings)
from app.intake import update_jobs, updater  # noqa: E402
from app.update_runner import Runner  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def s():
    return settings()


@pytest.fixture
def pipeline(monkeypatch):
    return FakePipeline(monkeypatch)


def runner(server, s, name="r1"):
    return Runner(make_client(server), s, es_mgr=object(), ontologies_dir="/data/ontologies",
                  es_url="http://es:9200", runner_id=name)


@pytest.fixture
async def client(server):
    c = make_client(server)
    await seed(c, entry("flopo"), entry("po"), entry("go"), entry("nosrc", source_url=None),
               entry("paused", update_status="disabled"))
    return c


async def submit(client, s, kind, ids, **kw):
    job, _ = await update_jobs.JobQueue(client, s).enqueue(kind, ids, **kw)
    return job["id"]


async def job(client, s, job_id):
    return await update_jobs.JobQueue(client, s).get(job_id)


class TestSingleJobs:

    async def test_update_runs_with_the_pipeline_arguments(self, server, s, client, pipeline):
        job_id = await submit(client, s, "update", ["flopo"], force=True)
        r = runner(server, s)
        ended, blocked = await r.run_once()
        assert ended["state"] == "done" and not blocked
        kind, oid, kw = pipeline.calls[0]
        assert (kind, oid, kw["force"], kw["ontologies_base_path"]) == ("update", "flopo", True, "/data/ontologies")
        record = await job(client, s, job_id)
        assert record["result"]["status"] == "unchanged" and record["current"] is None
        assert (await update_jobs.JobQueue(client, s).holder()) is None

    async def test_reindex(self, server, s, client, pipeline):
        await submit(client, s, "reindex", ["po"])
        await runner(server, s).run_once()
        assert pipeline.calls[0][:2] == ("reindex", "po") and "force" not in pipeline.calls[0][2]

    async def test_recovery_outcome_keeps_the_slot(self, server, s, client, pipeline):
        async def unknown(c):
            return await recovery_txn(c, "flopo")
        pipeline.outcomes["flopo"] = unknown
        first = await submit(client, s, "update", ["flopo"])
        await submit(client, s, "update", ["po"])
        r = runner(server, s)
        ended, _ = await r.run_once()
        assert ended["state"] == "recovery_required" and ended["held_by"] == "flopo"
        assert await r.run_once() == (None, True)
        assert pipeline.ids() == ["flopo"]
        holder = await update_jobs.JobQueue(client, s).holder()
        assert holder["job"] == first and holder["state"] == "recovery_required"

    async def test_an_exception_outside_the_pipeline_is_unknown(self, server, s, client, pipeline):
        pipeline.outcomes["flopo"] = RuntimeError("redis went away")
        await submit(client, s, "update", ["flopo"])
        ended, _ = await runner(server, s).run_once()
        assert ended["state"] == "recovery_required"
        assert ended["result"]["unknown_outcome"] is True and "unhandled" in ended["result"]["error"]

    async def test_result_holds_no_registry_secret(self, server, s, client, pipeline):
        job_id = await submit(client, s, "update", ["flopo"])
        await runner(server, s).run_once()
        assert "registry-secret" not in json.dumps(await job(client, s, job_id))

    async def test_an_unresolved_transaction_anywhere_blocks_claims(self, server, s, client, pipeline):
        await recovery_txn(client, "go")
        await submit(client, s, "update", ["flopo"])
        assert await runner(server, s).run_once() == (None, True)
        assert pipeline.calls == []


class TestBatch:

    async def test_sequential_with_full_coverage(self, server, s, client, pipeline):
        pipeline.outcomes["po"] = outcome("updated")
        job_id = await submit(client, s, "check", ["flopo", "po", "nosrc", "paused"],
                              skipped={"nope": "not_registered"})
        ended, _ = await runner(server, s).run_once()
        summary = ended["summary"]
        assert summary["unchanged"] == ["flopo"] and summary["updated"] == ["po"]
        assert summary["skipped"] == {"nope": "not_registered", "nosrc": "no_source_url", "paused": "disabled"}
        assert summary["not_run"] == [] and summary["failed"] == {}
        assert pipeline.ids() == ["flopo", "po"] and pipeline.peak == 1
        assert (await job(client, s, job_id))["state"] == "done"

    async def test_stops_at_an_unknown_outcome(self, server, s, client, pipeline):
        async def unknown(c):
            return await recovery_txn(c, "po")
        pipeline.outcomes["po"] = unknown
        await submit(client, s, "check", ["flopo", "po", "go"])
        ended, _ = await runner(server, s).run_once()
        assert ended["state"] == "recovery_required" and ended["held_by"] == "po"
        assert ended["summary"]["not_run"] == ["go"] and ended["summary"]["failed"] == {"po": "hotswap_unknown"}
        assert pipeline.ids() == ["flopo", "po"]

    async def test_cancel_stops_before_the_next_ontology(self, server, s, client, pipeline):
        pipeline.gate = asyncio.Event()
        job_id = await submit(client, s, "check", ["flopo", "po", "go"])
        task = asyncio.ensure_future(runner(server, s).run_once())
        await asyncio.wait_for(pipeline.started.wait(), 5)
        await update_jobs.JobQueue(client, s).cancel(job_id, "enough")
        pipeline.gate.set()
        ended, _ = await asyncio.wait_for(task, 5)
        assert ended["state"] == "cancelled" and ended["stop_reason"] == "cancelled"
        assert ended["summary"]["unchanged"] == ["flopo"] and ended["summary"]["not_run"] == ["po", "go"]
        assert (await update_jobs.JobQueue(client, s).holder()) is None


class TestInterruption:

    async def test_stop_during_a_job_keeps_the_slot(self, server, s, client, pipeline):
        pipeline.gate = asyncio.Event()
        job_id = await submit(client, s, "update", ["flopo"])
        r = runner(server, s)
        task = asyncio.ensure_future(r.run_once())
        await asyncio.wait_for(pipeline.started.wait(), 5)
        r.stop()
        ended, _ = await asyncio.wait_for(task, 5)
        assert ended["state"] == "recovery_required" and "cancelled" in ended["error"]
        holder = await update_jobs.JobQueue(client, s).holder()
        assert holder["job"] == job_id and holder["state"] == "recovery_required"

    async def test_two_runners_never_overlap(self, server, s, client, pipeline):
        for oid in ("flopo", "po", "go"):
            await submit(client, s, "update", [oid])
        a, b = runner(server, s, "a"), runner(server, s, "b")

        async def drain(r):
            for _ in range(20):
                await r.run_once()
        await asyncio.gather(drain(a), drain(b))
        assert sorted(pipeline.ids()) == ["flopo", "go", "po"] and pipeline.peak == 1

    async def test_api_stays_responsive_while_a_job_runs(self, server, s, client, pipeline):
        pipeline.gate = asyncio.Event()
        await submit(client, s, "update", ["flopo"])
        task = asyncio.ensure_future(runner(server, s).run_once())
        await asyncio.wait_for(pipeline.started.wait(), 5)
        other = update_jobs.JobQueue(make_client(server), s)
        status = await asyncio.wait_for(other.status(), 1)
        assert status["active"]["state"] == "running"
        assert (await asyncio.wait_for(other.enqueue("update", ["po"]), 1))[0]["state"] == "queued"
        pipeline.gate.set()
        await asyncio.wait_for(task, 5)

    async def test_heartbeat_fences_out_a_job_whose_claim_is_gone(self, server, s, client, pipeline):
        """The claim disappears while the job's pipeline waits (here the
        operator released it while the heartbeat looked stale). The next
        heartbeat cancels the job; nothing more is written."""
        pipeline.gate = asyncio.Event()
        first = await submit(client, s, "update", ["flopo"])
        r1 = runner(server, s, "r1")
        task = asyncio.ensure_future(r1.run_once())
        await asyncio.wait_for(pipeline.started.wait(), 5)
        q = update_jobs.JobQueue(client, s)
        claim = (await q.holder())["claim"]
        with pytest.raises(update_jobs.QueueError):
            await q.recover(first, claim, "looked", reconciled=True, runner_stopped=True)
        await age_heartbeat(client, s, "r1", s.stale_seconds + 1)
        await q.recover(first, claim, "runner unreachable; confirmed stopped", reconciled=True, runner_stopped=True)
        await r1.heartbeat_once(asyncio.get_running_loop().time())
        ended, _ = await asyncio.wait_for(task, 5)
        assert ended is None and r1.lost
        assert (await q.get(first))["state"] == "recovered" and await registry(client, "flopo") == entry("flopo")
        assert not pipeline.gate.is_set() and pipeline.in_flight == 0

    @pytest.mark.parametrize("failure", [RedisConnectionError("connection refused"),
                                         RedisTimeoutError("Timeout reading from socket")])
    async def test_redis_loss_during_a_job_fences_it_out(self, server, s, client, pipeline, monkeypatch, failure):
        """A Redis that refuses, or one that hangs until the client's socket
        timeout, counts as unreachable."""
        pipeline.gate = asyncio.Event()
        job_id = await submit(client, s, "update", ["flopo"])
        r1 = runner(server, s, "r1")
        task = asyncio.ensure_future(r1.run_once())
        await asyncio.wait_for(pipeline.started.wait(), 5)

        async def unreachable(*a, **k):
            raise failure
        monkeypatch.setattr(r1.queue, "heartbeat", unreachable)
        now = asyncio.get_running_loop().time()
        assert await r1.heartbeat_once(now) == now and not r1.lost, "a short outage is tolerated"
        await r1.heartbeat_once(now - r1.redis_loss_limit - 1)
        ended, _ = await asyncio.wait_for(task, 5)
        assert ended is None and r1.lost
        # Nothing was recorded: the job stays running and keeps the slot.
        assert (await job(client, s, job_id))["state"] == "running"


class TestStaleRunnerOnTheRealPipeline:
    """B1 with the real update pipeline (worker, source and ES fakes from
    test_update_pipeline.py): r1's update of flopo waits in its indexing
    poll, as a paused or partitioned runner would. The operator resolves
    flopo's transaction and releases the slot, r2 updates po, and then r1
    resumes. r1 must stop at its next step: no ES change, no worker request,
    no file change and no registry write."""

    @pytest.fixture
    def stack(self, monkeypatch, tmp_path):
        from tests.test_update_pipeline import OLD, FakeES, FakeWorker

        class Worker(FakeWorker):
            def __init__(self, es):
                super().__init__(es)
                self.hold = {}
                self.waiting = asyncio.Event()

            async def poll_task(self, server_url, task_id, timeout_secs=None):
                index = self._index_of.get(task_id, "")
                gate = next((g for oid, g in self.hold.items() if index.startswith(f"aberowl_{oid}_")), None)
                if gate is not None and not gate.is_set():
                    self.waiting.set()
                    await gate.wait()
                return await super().poll_task(server_url, task_id, timeout_secs)

        es = FakeES()
        w = Worker(es)
        for name, fn in (("worker_served_path", w.worker_served_path), ("check_version", w.check_version),
                         ("download_ontology", w.download), ("validate_staged", w.validate),
                         ("discard_export", w.discard), ("start_indexing", w.start_indexing),
                         ("start_hotswap", w.start_hotswap), ("poll_task", w.poll_task),
                         ("finalize_served_path", w.finalize), ("task_status_once", w.task_status_once)):
            monkeypatch.setattr(updater, name, fn)
        monkeypatch.setattr(updater, "_locks", {})
        # Separate processes do not share the in-process worker lock.
        monkeypatch.setattr(updater, "_worker_lock", lambda url: asyncio.Lock())
        monkeypatch.delenv("UPDATE_TRANSFER_CONFIG", raising=False)
        for oid in ("flopo", "po"):
            (tmp_path / oid).mkdir()
            (tmp_path / oid / f"{oid}.owl").write_bytes(OLD)
        return es, w, tmp_path, OLD

    async def test_a_released_runner_starts_no_further_step(self, server, s, stack):
        from tests.test_update_pipeline import NEW, flopo_entry, po_entry
        es, worker, corpus, old = stack
        client = make_client(server)
        await seed(client, flopo_entry(), po_entry())
        q = update_jobs.JobQueue(client, s)
        first = await submit(client, s, "update", ["flopo"])
        await submit(client, s, "update", ["po"])
        worker.hold["flopo"] = asyncio.Event()

        def make(name):
            return Runner(make_client(server), s, es, str(corpus), "http://es:9200", runner_id=name)
        r1, r2 = make("r1"), make("r2")
        task = asyncio.ensure_future(r1.run_once())
        await asyncio.wait_for(worker.waiting.wait(), 5)
        txn = (await registry(client, "flopo"))["update_txn"]
        assert txn["phase"] == "index" and txn["task_id"]

        # Operator: r1 is silent, the worker task is reconciled, the slot released.
        await age_heartbeat(client, s, "r1", s.stale_seconds + 1)
        claim = (await q.holder())["claim"]
        listed = await q.recover(first, claim, "r1 stopped", reconciled=True, runner_stopped=True)
        assert listed["unresolved"][0]["txn_id"] == txn["id"]
        assert await updater.resolve_transaction(client, "flopo", txn["id"], "index reconciled", "unknown_task")
        assert (await q.recover(first, claim, "txn resolved", reconciled=True,
                                runner_stopped=False))["status"] == "released"

        ended2, _ = await r2.run_once()
        assert ended2["state"] == "done" and ended2["result"]["status"] == "updated"
        assert (corpus / "po" / "po.owl").read_bytes() == NEW

        resolved = await registry(client, "flopo")
        es_calls, events = list(es.calls), len(worker.events)
        aliases = dict(es.aliases)
        worker.hold["flopo"].set()
        ended1, _ = await asyncio.wait_for(task, 5)

        assert ended1 is None and r1.lost
        assert es.calls == es_calls and es.aliases == aliases, "r1 changed Elasticsearch after its release"
        assert [e for e in worker.events[events:] if e[0] != "poll"] == [], worker.events[events:]
        assert await registry(client, "flopo") == resolved, "r1 wrote the registry after its release"
        assert "update_txn" not in resolved and resolved["update_status"] == "resolved_by_operator"
        assert (corpus / "flopo" / "flopo.owl").read_bytes() == old
        assert (corpus / "flopo" / "updates" / txn["id"]).is_dir(), "the stage is the operator's to remove"
        assert (await q.get(first))["state"] == "recovered"


class TestProcess:

    async def test_run_forever_exits_3_when_the_claim_disappears(self, server, s, client, pipeline):
        pipeline.gate = asyncio.Event()
        await submit(client, s, "update", ["flopo"])
        r = runner(server, s)
        r.heartbeat_interval = 0.02
        task = asyncio.ensure_future(r.run_forever())
        await asyncio.wait_for(pipeline.started.wait(), 5)
        q = update_jobs.JobQueue(client, s)
        await client.hdel(q.k_state, "active")  # whatever took it, the claim is gone
        assert await asyncio.wait_for(task, 5) == 3
        assert not pipeline.gate.is_set() and await client.hget(q.k_runners, "r1") is None

    async def test_run_forever_idle_stop_exits_0(self, server, s, client):
        r = runner(server, s)
        r.heartbeat_interval = 0.02
        task = asyncio.ensure_future(r.run_forever())
        q = update_jobs.JobQueue(client, s)
        for _ in range(200):
            if await client.hget(q.k_runners, "r1"):
                break
            await asyncio.sleep(0.01)
        beat = json.loads(await client.hget(q.k_runners, "r1"))
        assert beat["settings"] == json.loads(json.dumps(s.shared()))
        r.stop()
        assert await asyncio.wait_for(task, 5) == 0
        assert await client.hget(q.k_runners, "r1") is None

    async def test_a_failing_heartbeat_during_a_job_stops_the_runner(self, server, s, client, pipeline,
                                                                     monkeypatch):
        pipeline.gate = asyncio.Event()
        job_id = await submit(client, s, "update", ["flopo"])
        r = runner(server, s)
        r.heartbeat_interval = 0.02
        task = asyncio.ensure_future(r.run_forever())
        await asyncio.wait_for(pipeline.started.wait(), 5)

        async def broken(*a, **k):
            raise ValueError("a bug in the heartbeat")
        monkeypatch.setattr(r.queue, "heartbeat", broken)
        assert await asyncio.wait_for(task, 5) == 3
        assert not pipeline.gate.is_set() and pipeline.in_flight == 0
        assert (await job(client, s, job_id))["state"] == "running", "nothing written: recovery decides"

    async def test_a_failing_heartbeat_while_idle_stops_the_runner(self, server, s, client, monkeypatch):
        r = runner(server, s)

        async def broken(*a, **k):
            raise ValueError("a bug in the heartbeat")
        monkeypatch.setattr(r.queue, "heartbeat", broken)
        assert await asyncio.wait_for(r.run_forever(), 5) == 3
        assert r.lost and r.stopping

    def test_the_runner_client_has_bounded_timeouts(self):
        from app.update_runner import redis_client
        c = redis_client(settings(UPDATE_RUNNER_HEARTBEAT_SECONDS=2, UPDATE_RUNNER_STALE_SECONDS=10))
        kwargs = c.connection_pool.connection_kwargs
        assert kwargs["socket_timeout"] == 2 and kwargs["socket_connect_timeout"] == 2

    def test_invalid_configuration_exits_2(self, monkeypatch):
        from app import update_runner
        monkeypatch.setenv("UPDATE_EXECUTION", "queued")
        monkeypatch.setenv("UPDATE_MAX_EXPANDED_BYTES", "0")
        with pytest.raises(SystemExit) as e:
            update_runner.main()
        assert e.value.code == 2
