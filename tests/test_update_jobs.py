"""
The update queue and the global update slot (central_server/app/intake/
update_jobs.py): settings, atomic claim across separate connections,
duplicates, retention, holding the slot through unknown outcomes, operator
recovery, and the exclusion between inline and queued execution.

fakeredis stands in for Redis; each client is a separate connection to one
server, as separate processes would be. test_update_jobs_redis.py repeats
the concurrency checks with real processes against a real Redis.
"""

import asyncio
import json

import pytest

from tests.update_job_helpers import (Interleaved, age_heartbeat, entry, fakeredis, make_client,  # noqa: F401
                                      registry, seed, settings, write_txn)
from app.intake import update_jobs, updater  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def s():
    return settings()


def queue(server, s):
    return update_jobs.JobQueue(make_client(server), s)


class TestSettings:

    def test_default_is_inline(self):
        s = update_jobs.load_settings({})
        assert s.mode == "inline" and s.error is None and s.redis_url == "redis://redis"

    @pytest.mark.parametrize("env,fragment", [
        ({"UPDATE_EXECUTION": "queue"}, "UPDATE_EXECUTION"),
        ({"UPDATE_EXECUTION": "queued", "UPDATE_WAIT_SECONDS": "soon"}, "UPDATE_WAIT_SECONDS"),
        ({"UPDATE_EXECUTION": "queued", "UPDATE_QUEUE_MAX": "0"}, "UPDATE_QUEUE_MAX"),
        ({"UPDATE_EXECUTION": "queued", "UPDATE_RUNNER_HEARTBEAT_SECONDS": "60",
          "UPDATE_RUNNER_STALE_SECONDS": "90"}, "three heartbeats"),
        ({"UPDATE_EXECUTION": "inline", "UPDATE_QUEUE_PREFIX": "a b"}, "UPDATE_QUEUE_PREFIX"),
    ])
    def test_invalid_settings_never_fall_back(self, env, fragment):
        s = update_jobs.load_settings(env)
        assert s.mode == "invalid" and fragment in s.error

    def test_redis_url_is_configurable(self):
        assert update_jobs.load_settings({"REDIS_URL": "redis://cache:6380/2"}).redis_url == "redis://cache:6380/2"

    @pytest.mark.parametrize("value", ["0", "-5", "lots", "1.5", str((1 << 40) + 1)])
    @pytest.mark.parametrize("name", ["UPDATE_MAX_DOWNLOAD_BYTES", "UPDATE_MAX_EXPANDED_BYTES"])
    def test_download_limits_must_be_positive_byte_counts(self, name, value):
        s = update_jobs.load_settings({"UPDATE_EXECUTION": "queued", name: value})
        assert s.mode == "invalid" and name in s.error
        assert updater.byte_limit(value, 10) is None

    @pytest.mark.parametrize("value", ["0", "29", "86401", "-1", "nan", "inf", "1800.5", "later"])
    def test_index_timeout_refuses_invalid_values(self, value):
        s = update_jobs.load_settings({"UPDATE_INDEX_TIMEOUT_SECONDS": value})
        assert s.mode == "invalid" and "UPDATE_INDEX_TIMEOUT_SECONDS" in s.error

    @pytest.mark.parametrize("value,expected", [(None,1800),(" ",1800),("30",30),("10800",10800),("86400",86400)])
    def test_index_timeout_is_shared_between_api_and_runner(self, value, expected):
        env = {} if value is None else {"UPDATE_INDEX_TIMEOUT_SECONDS": value}
        s = update_jobs.load_settings(env)
        assert s.error is None and s.shared()["index_timeout_seconds"] == expected

    def test_download_limits(self):
        s = update_jobs.load_settings({"UPDATE_MAX_DOWNLOAD_BYTES": "1000"})
        assert s.error is None and s.max_download_bytes == 1000 and s.max_expanded_bytes == 8 << 30
        assert updater.byte_limit(None, 10) == 10 and updater.byte_limit(" ", 10) == 10


class TestSubmission:

    async def test_duplicate_of_a_queued_or_running_job_returns_it(self, server, s):
        q = queue(server, s)
        job, dup = await q.enqueue("update", ["flopo"])
        again, dup2 = await q.enqueue("update", ["flopo"])
        assert not dup and dup2 and again["id"] == job["id"]
        other, dup3 = await q.enqueue("update", ["flopo"], force=True)
        assert not dup3 and other["id"] != job["id"]
        claimed, _ = await q.claim("r1")
        assert claimed["id"] == job["id"]
        running, dup4 = await q.enqueue("update", ["flopo"])
        assert dup4 and running["id"] == job["id"] and running["state"] == "running"
        await q.finish(job["id"], claimed["claim"], hold=False)
        after, dup5 = await q.enqueue("update", ["flopo"])
        assert not dup5 and after["id"] != job["id"]

    async def test_queue_is_bounded(self, server):
        q = queue(server, settings(UPDATE_QUEUE_MAX=2))
        await q.enqueue("update", ["a"])
        await q.enqueue("update", ["b"])
        with pytest.raises(update_jobs.QueueError) as e:
            await q.enqueue("update", ["c"])
        assert e.value.status == 503

    async def test_concurrent_equal_submissions_make_one_job(self, server, s):
        clients = [queue(server, s) for _ in range(8)]
        results = await asyncio.gather(*[q.enqueue("update", ["flopo"]) for q in clients])
        assert len({job["id"] for job, _ in results}) == 1
        assert sum(1 for _, dup in results if not dup) == 1
        assert await clients[0].pending_count() == 1

    async def test_scheduled_submission_once_per_interval(self, server, s):
        q = queue(server, s)
        job, _ = await q.enqueue("check", ["flopo"], source="scheduled", schedule_interval=3600)
        claimed, _ = await q.claim("r1")
        await q.finish(job["id"], claimed["claim"], hold=False)
        assert await q.enqueue("check", ["flopo"], source="scheduled", schedule_interval=3600) == (None, False)
        assert (await q.enqueue("check", ["flopo"], source="scheduled", schedule_interval=0))[0] is not None

    async def test_check_job_records_unknown_ids(self, server, s):
        job, _ = await queue(server, s).enqueue("check", ["flopo"], skipped={"nope": "not_registered"})
        assert job["summary"]["skipped"] == {"nope": "not_registered"} and job["summary"]["not_run"] == []


class TestClaim:

    async def test_one_claim_among_concurrent_runners(self, server, s):
        q0 = queue(server, s)
        for oid in ("a", "b", "c"):
            await q0.enqueue("update", [oid])
        runners = [queue(server, s) for _ in range(10)]
        results = await asyncio.gather(*[q.claim(f"r{i}") for i, q in enumerate(runners)])
        claimed = [job for job, _ in results if job]
        assert len(claimed) == 1 and claimed[0]["ontologies"] == ["a"]
        assert all("holds the global slot" in reason for job, reason in results if not job)
        assert await q0.pending_count() == 2

    async def test_fifo_and_release(self, server, s):
        q = queue(server, s)
        ids = [(await q.enqueue("update", [oid]))[0]["id"] for oid in ("a", "b")]
        first, _ = await q.claim("r1")
        assert first["id"] == ids[0]
        assert (await q.claim("r2"))[0] is None
        ended = await q.finish(first["id"], first["claim"], hold=False, result={"status": "unchanged"})
        assert ended["state"] == "done"
        second, _ = await q.claim("r2")
        assert second["id"] == ids[1]
        assert int(second["claim"].split(":")[0]) > int(first["claim"].split(":")[0])

    async def test_nothing_queued(self, server, s):
        assert await queue(server, s).claim("r1") == (None, None)

    async def test_held_outcome_keeps_the_slot(self, server, s):
        q = queue(server, s)
        job, _ = await q.enqueue("update", ["a"])
        await q.enqueue("update", ["b"])
        claimed, _ = await q.claim("r1")
        held = await q.finish(job["id"], claimed["claim"], hold=True, held_by="a")
        assert held["state"] == "recovery_required"
        assert (await q.claim("r2"))[0] is None
        with pytest.raises(update_jobs.OwnershipLost):
            await q.progress(job["id"], claimed["claim"], current="b")

    async def test_cancelled_jobs_are_skipped(self, server, s):
        q = queue(server, s)
        a, _ = await q.enqueue("update", ["a"])
        b, _ = await q.enqueue("update", ["b"])
        cancelled = await q.cancel(a["id"], "not needed")
        assert cancelled["state"] == "cancelled"
        claimed, _ = await q.claim("r1")
        assert claimed["id"] == b["id"]
        with pytest.raises(update_jobs.QueueError) as e:
            await q.cancel(b["id"])
        assert e.value.status == 409

    async def test_running_batch_cancel_is_requested(self, server, s):
        q = queue(server, s)
        job, _ = await q.enqueue("check", ["a", "b"])
        await q.claim("r1")
        assert (await q.cancel(job["id"]))["cancel_requested"] is True

    async def test_retention(self, server):
        q = queue(server, settings(UPDATE_JOB_RETAIN=3))
        ids = []
        for oid in "abcde":
            job, _ = await q.enqueue("update", [oid])
            claimed, _ = await q.claim("r1")
            await q.finish(job["id"], claimed["claim"], hold=False)
            ids.append(job["id"])
        status = await q.status()
        assert [j["id"] for j in status["recent"]] == ids[::-1][:3]
        assert await q.get(ids[0]) is None and await q.get(ids[-1]) is not None


class TestRecovery:

    @pytest.fixture
    async def held(self, server, s):
        """A runner claimed an update of a, heartbeats, and is then lost."""
        q = queue(server, s)
        await seed(q.r, entry("a"), entry("b"))
        job, _ = await q.enqueue("update", ["a"])
        claimed, _ = await q.claim("r1")
        await q.progress(job["id"], claimed["claim"], current="a")
        await q.heartbeat("r1", {"runner": "r1"})
        await q.enqueue("update", ["b"])
        return q, job["id"], claimed["claim"]

    async def test_refusals(self, held, server, s):
        q, job_id, claim = held
        with pytest.raises(update_jobs.QueueError) as e:
            await q.recover(job_id, claim, "", reconciled=True, runner_stopped=True)
        assert e.value.status == 422
        with pytest.raises(update_jobs.QueueError) as e:
            await q.recover(job_id, "1:someone-else", "checked", reconciled=True, runner_stopped=True)
        assert e.value.status == 409
        with pytest.raises(update_jobs.QueueError) as e:
            await q.recover(job_id, claim, "checked", reconciled=True, runner_stopped=False)
        assert e.value.status == 409 and "runner_stopped" in str(e.value.detail)
        with pytest.raises(update_jobs.QueueError) as e:
            await q.recover(job_id, claim, "checked", reconciled=True, runner_stopped=True)
        assert e.value.status == 409 and "heartbeat" in str(e.value.detail)
        assert (await q.claim("r2"))[0] is None

    async def test_stale_runner_reconnecting_writes_nothing(self, held, server, s):
        q, job_id, claim = held
        await age_heartbeat(q.r, s, "r1", s.stale_seconds + 1)
        released = await q.recover(job_id, claim, "runner container stopped; nothing ran", reconciled=True,
                                   runner_stopped=True)
        assert released["status"] == "released"
        record = await q.get(job_id)
        assert record["state"] == "recovered" and [e["event"] for e in record["events"]] == ["marked_lost",
                                                                                              "released"]
        other = queue(server, s)
        nxt, _ = await other.claim("r2")
        assert nxt["ontologies"] == ["b"]
        with pytest.raises(update_jobs.OwnershipLost):
            await q.progress(job_id, claim, current="a")
        with pytest.raises(update_jobs.OwnershipLost):
            await q.finish(job_id, claim, hold=False, result={"status": "updated"})
        assert (await q.get(job_id))["state"] == "recovered"
        assert (await other.progress(nxt["id"], nxt["claim"], current="b"))["state"] == "running"

    async def test_transactions_are_resolved_first(self, held, server, s):
        q, job_id, claim = held
        e = await registry(q.r, "a")
        e["update_txn"] = {"id": "txn-1", "state": "running", "phase": "index", "task_id": "idx-1"}
        await q.r.hset(update_jobs.REGISTRY_KEY, "a", json.dumps(e))
        await age_heartbeat(q.r, s, "r1", s.stale_seconds + 1)
        first = await q.recover(job_id, claim, "runner lost", reconciled=True, runner_stopped=True)
        assert first["status"] == "recovery_required" and first["unresolved"][0]["txn_id"] == "txn-1"
        with pytest.raises(update_jobs.QueueError) as err:
            await q.recover(job_id, claim, "again", reconciled=True, runner_stopped=False)
        assert err.value.status == 409 and err.value.detail["unresolved"][0]["ontology_id"] == "a"
        assert (await q.claim("r2"))[0] is None
        e.pop("update_txn")
        await q.r.hset(update_jobs.REGISTRY_KEY, "a", json.dumps(e))
        assert (await q.recover(job_id, claim, "resolved txn-1", reconciled=True,
                                runner_stopped=False))["status"] == "released"
        assert (await q.claim("r2"))[0]["ontologies"] == ["b"]

    async def test_only_the_holder_can_be_recovered(self, held, server, s):
        q, _, _ = held
        with pytest.raises(update_jobs.QueueError) as e:
            await q.recover("uj-other", "1:r1", "x", reconciled=True, runner_stopped=True)
        assert e.value.status == 409


class TestInlineExclusion:

    async def test_runner_does_not_claim_beside_an_inline_run(self, server, s):
        q = queue(server, s)
        gate = update_jobs.InlineGate("api-1")
        marker = await gate.begin(q, gate.reserve(), "update", ["a"])
        await q.enqueue("update", ["b"])
        job, reason = await q.claim("r1")
        assert job is None and "inline" in reason
        await gate.run(q, marker, self._ok)
        assert (await q.claim("r1"))[0] is not None

    @staticmethod
    async def _ok(marker):
        return {"status": "unchanged"}, None

    async def test_inline_refuses_beside_a_queued_job_or_runner(self, server, s):
        q = queue(server, s)
        await q.enqueue("update", ["a"])
        await q.claim("r1")
        gate = update_jobs.InlineGate("api-1")
        with pytest.raises(update_jobs.QueueError) as e:
            await gate.begin(q, gate.reserve(), "update", ["b"])
        assert "prohibited" in e.value.detail and gate.job is None
        assert await q.inline_marker() is None

        idle = queue(fakeredis.FakeServer(), s)
        await idle.heartbeat("r9", {"runner": "r9"})
        with pytest.raises(update_jobs.QueueError):
            await gate.begin(idle, gate.reserve(), "update", ["b"])

    async def test_interleaved_inline_and_claim_never_both_win(self, server, s):
        for round_ in range(25):
            srv = fakeredis.FakeServer()
            q_runner, q_api = queue(srv, s), queue(srv, s)
            await q_runner.enqueue("update", ["a"])
            gate = update_jobs.InlineGate("api-1")

            async def inline():
                try:
                    await gate.begin(q_api, gate.reserve(), "update", ["b"])
                    return True
                except update_jobs.QueueError:
                    return False
            won_inline, (job, _) = await asyncio.gather(inline(), q_runner.claim("r1"))
            assert not (won_inline and job), f"round {round_}: both ran"

    async def test_a_held_inline_run_blocks_the_next(self, server, s):
        q = queue(server, s)
        gate = update_jobs.InlineGate("api-1")
        marker = await gate.begin(q, gate.reserve(), "update", ["a"])

        async def held(marker):
            return {"status": "failed", "recovery_required": True}, "a"
        await gate.run(q, marker, held)
        with pytest.raises(update_jobs.QueueError) as e:
            await gate.begin(q, gate.reserve(), "update", ["b"])
        assert "recover" in e.value.detail
        kept = await q.inline_marker()
        assert kept["state"] == "recovery_required" and kept["held_by"] == "a"
        assert (await q.recover(kept["job"], kept["claim"], "checked", reconciled=True,
                                runner_stopped=False))["status"] == "released"
        assert await q.inline_marker() is None
        assert (await q.get(kept["job"]))["state"] == "recovered"

    async def test_one_inline_job_per_process(self):
        gate = update_jobs.InlineGate("api-1")
        gate.reserve()
        with pytest.raises(update_jobs.QueueError) as e:
            gate.reserve()
        assert e.value.status == 409


class TestFencedPipelineClient:
    """B1: the pipeline's registry writes and checkpoints hold only while
    its job holds the slot with its claim."""

    @pytest.fixture
    async def released(self, server, s):
        """r1 claimed an update of a and was released by recovery (its
        heartbeat stale); r2 then claimed the update of b."""
        q = queue(server, s)
        await seed(q.r, entry("a"), entry("b"))
        job_a, _ = await q.enqueue("update", ["a"])
        await q.enqueue("update", ["b"])
        claimed, _ = await q.claim("r1")
        fenced = update_jobs.FencedRedis(q, job_a["id"], claimed["claim"])
        before = await registry(q.r, "a")
        before["note"] = "written while held"
        await fenced.hset(update_jobs.REGISTRY_KEY, "a", json.dumps(before))
        assert (await registry(q.r, "a"))["note"] == "written while held"
        await q.recover(job_a["id"], claimed["claim"], "r1 stopped", reconciled=True, runner_stopped=True)
        other = queue(server, s)
        job_b, _ = await other.claim("r2")
        return q, fenced, other, job_b

    async def test_stale_writes_and_steps_are_refused_while_the_next_runner_works(self, released):
        q, fenced, other, job_b = released
        snapshot = await registry(q.r, "a")
        stale = dict(snapshot, update_txn={"id": "txn-old", "state": "running"})
        with pytest.raises(update_jobs.OwnershipLost):
            await fenced.hset(update_jobs.REGISTRY_KEY, "a", json.dumps(stale))
        with pytest.raises(update_jobs.OwnershipLost):
            await fenced.check_claim()
        with pytest.raises(update_jobs.OwnershipLost):
            await updater._checkpoint(fenced)
        # A phase start whose record is gone writes nothing, and still stops.
        with pytest.raises(update_jobs.OwnershipLost):
            await updater._txn_set(fenced, "a", "txn-old", phase="hotswap")
        # The pipeline's failure and recovery records are refused too.
        with pytest.raises(update_jobs.OwnershipLost):
            await updater._recovery(fenced, "a", "txn-old", "unexpected", "x")
        assert await registry(q.r, "a") == snapshot
        fenced_b = update_jobs.FencedRedis(other, job_b["id"], job_b["claim"])
        await updater._checkpoint(fenced_b)
        await fenced_b.hset(update_jobs.REGISTRY_KEY, "b", json.dumps(entry("b", note="r2")))
        assert (await registry(q.r, "b"))["note"] == "r2"

    async def test_release_between_read_and_write_refuses_the_write(self, server, s):
        q = queue(server, s)
        await seed(q.r, entry("a"))
        job, _ = await q.enqueue("update", ["a"])
        claimed, _ = await q.claim("r1")
        operator = queue(server, s)

        async def release():
            await operator.recover(job["id"], claimed["claim"], "released", reconciled=True, runner_stopped=True)
        interleaved = update_jobs.JobQueue(Interleaved(make_client(server), q.k_state, release), s)
        fenced = update_jobs.FencedRedis(interleaved, job["id"], claimed["claim"])
        with pytest.raises(update_jobs.OwnershipLost):
            await fenced.hset(update_jobs.REGISTRY_KEY, "a", json.dumps(entry("a", note="late")))
        assert "note" not in await registry(q.r, "a")

    async def test_only_registry_writes_and_reads_are_offered(self, server, s):
        fenced = update_jobs.FencedRedis(queue(server, s), "uj-x", "1:r1")
        with pytest.raises(ValueError):
            await fenced.hset("other", "k", "v")
        with pytest.raises(AttributeError):
            fenced.delete  # noqa: B018
        assert await fenced.hget(update_jobs.REGISTRY_KEY, "nothing") is None


class TestGuardedRegistryChange:
    """B2: the job guard and the registry write are one transaction."""

    async def test_queued_running_and_held_jobs_block_a_jobs_guard(self, server, s):
        q = queue(server, s)
        await seed(q.r, entry("a"), entry("b"))
        job, _ = await q.enqueue("check", ["a", "b"])
        for oid in ("a", "b"):
            with pytest.raises(update_jobs.QueueError) as e:
                await q.mutate_entry(oid, lambda x: x.update(update_status="disabled"), guard="jobs")
            assert e.value.status == 409 and "queued" in e.value.detail and "cancel" in e.value.detail
        claimed, _ = await q.claim("r1")
        with pytest.raises(update_jobs.QueueError):
            await q.mutate_entry("b", lambda x: None, guard="jobs")
        await q.finish(job["id"], claimed["claim"], hold=True, held_by="a")
        with pytest.raises(update_jobs.QueueError):
            await q.mutate_entry("a", lambda x: None, guard="jobs")
        # A held job does not block resolving its own transaction.
        _, written = await q.mutate_entry("a", lambda x: x.update(resolved=True), guard="running")
        assert written and (await registry(q.r, "a"))["resolved"] is True

    async def test_a_claim_between_the_guard_read_and_the_write_is_refused(self, server, s):
        q = queue(server, s)
        await seed(q.r, entry("a"))
        runner_side = queue(server, s)

        async def queue_and_claim():
            await runner_side.enqueue("update", ["a"])
            await runner_side.claim("r1")
        api = update_jobs.JobQueue(Interleaved(make_client(server), update_jobs.REGISTRY_KEY, queue_and_claim), s)
        with pytest.raises(update_jobs.QueueError) as e:
            await api.mutate_entry("a", lambda x: x.update(source_url="https://example.org/new.owl"), guard="jobs")
        assert e.value.status == 409 and api.r.fired == 1
        assert (await registry(q.r, "a"))["source_url"] == entry("a")["source_url"]

    async def test_an_unguarded_merge_keeps_a_concurrent_runner_write(self, server, s):
        q = queue(server, s)
        await seed(q.r, entry("a"))
        other = make_client(server)
        api = update_jobs.JobQueue(Interleaved(make_client(server), update_jobs.REGISTRY_KEY,
                                               lambda: write_txn(other, "a")), s)
        merged, written = await api.mutate_entry("a", lambda x: x.update(status="offline"))
        final = await registry(q.r, "a")
        assert written and final["status"] == "offline"
        assert final["update_txn"]["id"] == "txn-runner" and final["source_md5"] == "md5-from-runner"

    async def test_create_and_skip(self, server, s):
        q = queue(server, s)
        assert await q.mutate_entry("new", lambda x: x.update(a=1)) == (None, False)
        entry_, written = await q.mutate_entry("new", lambda x: x.update(a=1), create=True)
        assert written and entry_ == {"a": 1}
        _, written = await q.mutate_entry("new", lambda x: False)
        assert not written


class TestRunnerHeartbeats:

    async def test_stale_entries_are_pruned(self, server, s):
        q = queue(server, s)
        await q.r.hset(q.k_runners, "crashed", json.dumps({"at": "2000-01-01T00:00:00+00:00"}))
        await q.r.hset(q.k_runners, "garbage", "not json")
        await q.heartbeat("alive", {"runner": "alive"})
        assert sorted(await q.r.hkeys(q.k_runners)) == ["alive"]

    async def test_status_flags_settings_that_differ(self, server, s):
        q = queue(server, s)
        await q.heartbeat("same", {"runner": "same", "settings": s.shared()})
        await q.heartbeat("other", {"runner": "other", "settings": {**s.shared(), "stale_seconds": 999}})
        flags = {r["runner"]: r["settings_match"] for r in (await q.status())["runners"]}
        assert flags == {"same": True, "other": False}


def test_sanitize_removes_credential_fields():
    out = update_jobs.sanitize({"status": "failed", "secret_key": "x", "nested": [{"Authorization": "Bearer y"}],
                                "message": "kept"})
    assert out == {"status": "failed", "secret_key": "[redacted]", "nested": [{"Authorization": "[redacted]"}],
                   "message": "kept"}
