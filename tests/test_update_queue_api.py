"""
The public API's update entry points with UPDATE_EXECUTION=queued, invalid
and inline: every trigger (trigger_update, webhook, check_updates, reindex,
the scheduled check, the internal _run_update and _check_and_update_all)
either queues a job or, in inline mode, runs as the process's only heavy
job; nothing runs in the API process in queued mode, an invalid setting
refuses everything, waits are bounded, and the global slot's holder guards
the ontology state in every API process.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from tests.update_job_helpers import (SECRET, FakePipeline, Interleaved, entry, fakeredis, make_client,  # noqa: F401
                                      outcome, recovery_txn, registry, seed, settings, write_txn)
from app.intake import update_jobs, updater  # noqa: E402
from app.update_runner import Runner  # noqa: E402

pytestmark = pytest.mark.unit

AUTH = ("test-admin", "test-pw")


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
async def m(monkeypatch, server, tmp_path):
    import app.main as m
    monkeypatch.setattr(m, "ADMIN_USER", AUTH[0])
    monkeypatch.setattr(m, "ADMIN_PASSWORD", AUTH[1])
    monkeypatch.setattr(m, "es_mgr", MagicMock())
    monkeypatch.setattr(m, "ONTOLOGIES_DIR", str(tmp_path))
    monkeypatch.setattr(m, "UPDATE_ONTOLOGIES", [])
    monkeypatch.setattr(m, "UPDATE_SETTINGS", settings())
    monkeypatch.setattr(m, "_inline_gate", update_jobs.InlineGate("api-test"))
    client = make_client(server)
    await seed(client, entry("flopo"), entry("po"), entry("nosrc", source_url=None))
    monkeypatch.setattr(m, "redis_client", client)
    return m


def http(m, auth=AUTH):
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test", auth=auth)


@pytest.fixture
def forbidden(monkeypatch):
    """Any heavy update work in this process fails the test."""
    async def heavy(*a, **k):
        raise AssertionError("the API process ran update work")
    for name in ("execute_update_pipeline", "execute_reindex", "download_ontology", "validate_staged",
                 "start_indexing", "start_hotswap", "check_version"):
        monkeypatch.setattr(updater, name, heavy)


def runner(server, m, name="r1"):
    return Runner(make_client(server), m.UPDATE_SETTINGS, es_mgr=object(), ontologies_dir=m.ONTOLOGIES_DIR,
                  es_url="http://es:9200", runner_id=name)


def queue(m):
    return update_jobs.JobQueue(m.redis_client, m.UPDATE_SETTINGS)


@pytest.mark.usefixtures("forbidden")
class TestQueuedEntryPoints:

    async def test_every_trigger_queues_and_nothing_runs_here(self, m):
        async with http(m) as c:
            t = await c.post("/admin/ontology/flopo/trigger_update")
            r = await c.post("/admin/ontology/po/reindex")
            b = await c.post("/admin/check_updates", params={"ontologies": "flopo,nosrc,nope"})
        async with http(m, auth=None) as c:
            w = await c.post("/api/webhook/update/flopo", headers={"X-Webhook-Secret": SECRET})
        assert [x.status_code for x in (t, r, b, w)] == [202, 202, 202, 202]
        assert t.json()["status"] == "update_queued" and r.json()["status"] == "reindex_queued"
        assert w.json()["job_id"] == t.json()["job_id"] and w.json()["duplicate"] is True
        batch = await queue(m).get(b.json()["job_id"])
        assert batch["ontologies"] == ["flopo", "nosrc"] and batch["summary"]["skipped"] == {"nope": "not_registered"}
        assert await queue(m).pending_count() == 3

    async def test_internal_paths_refuse_to_run_inline(self, m):
        for call in (m._run_update("flopo"), m._run_reindex("flopo"), m._check_and_update_all()):
            with pytest.raises(update_jobs.QueueError) as e:
                await call
            assert e.value.status == 409
        assert m._inline_gate.job is None

    async def test_scheduled_check_is_queued_once(self, m):
        await m._scheduled_update_check()
        await m._scheduled_update_check()
        status = await queue(m).status()
        assert status["pending_count"] == 1 and status["pending"][0]["source"] == "scheduled"

    async def test_duplicate_requests(self, m):
        async with http(m) as c:
            a = (await c.post("/admin/ontology/flopo/trigger_update")).json()
            b = (await c.post("/admin/ontology/flopo/trigger_update")).json()
            f = (await c.post("/admin/ontology/flopo/trigger_update", params={"force": "true"})).json()
        assert a["job_id"] == b["job_id"] and b["duplicate"] and not a["duplicate"]
        assert f["job_id"] != a["job_id"]

    async def test_wait_without_a_runner_answers_with_the_job(self, m, monkeypatch):
        monkeypatch.setattr(m, "UPDATE_SETTINGS", settings(UPDATE_WAIT_SECONDS=0.3))
        async with http(m) as c:
            r = await c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true"})
        body = r.json()
        assert r.status_code == 202 and body["job_state"] == "queued"
        assert "success" not in body and "goes on" in body["detail"] and "warning" in body

    async def test_a_client_that_stops_waiting_leaves_the_job(self, m, monkeypatch):
        monkeypatch.setattr(m, "UPDATE_SETTINGS", settings(UPDATE_WAIT_SECONDS=30))
        async with http(m) as c:
            task = asyncio.ensure_future(c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true"}))
            for _ in range(200):
                if await queue(m).pending_count():
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        status = await queue(m).status()
        assert status["pending_count"] == 1 and status["pending"][0]["state"] == "queued"

    async def test_preflight_still_refuses(self, m):
        e = await registry(m.redis_client, "po")
        e["update_txn"] = {"id": "txn-7", "state": "recovery_required"}
        await m.redis_client.hset(update_jobs.REGISTRY_KEY, "po", json.dumps(e))
        async with http(m) as c:
            assert (await c.post("/admin/ontology/nosrc/trigger_update")).status_code == 409
            assert (await c.post("/admin/ontology/po/trigger_update")).status_code == 409
            assert (await c.post("/admin/ontology/po/reindex")).status_code == 409
        assert await queue(m).pending_count() == 0

    async def test_auth(self, m):
        async with http(m, auth=None) as c:
            assert (await c.post("/admin/ontology/flopo/trigger_update")).status_code == 401
            assert (await c.post("/admin/check_updates")).status_code == 401
            assert (await c.get("/admin/update_jobs")).status_code == 401
            assert (await c.post("/admin/update_jobs/x/recover", json={"claim": "c"})).status_code == 401
            assert (await c.post("/api/webhook/update/flopo", headers={"X-Webhook-Secret": "wrong"})).status_code == 401
        assert await queue(m).pending_count() == 0


@pytest.mark.usefixtures("forbidden")
class TestInvalidConfiguration:

    async def test_everything_is_refused(self, m, monkeypatch):
        monkeypatch.setattr(m, "UPDATE_SETTINGS", update_jobs.load_settings({"UPDATE_EXECUTION": "queue"}))
        async with http(m) as c:
            codes = [(await c.post(p)).status_code for p in (
                "/admin/ontology/flopo/trigger_update", "/admin/ontology/flopo/reindex", "/admin/check_updates")]
        async with http(m, auth=None) as c:
            codes.append((await c.post("/api/webhook/update/flopo", headers={"X-Webhook-Secret": SECRET})).status_code)
        assert codes == [503, 503, 503, 503]
        for call in (m._run_update("flopo"), m._check_and_update_all()):
            with pytest.raises(update_jobs.QueueError) as e:
                await call
            assert e.value.status == 503
        assert await queue(m).pending_count() == 0


class TestQueuedWithRunner:

    async def test_wait_returns_the_outcome(self, m, server, monkeypatch):
        FakePipeline(monkeypatch)
        monkeypatch.setattr(m, "UPDATE_SETTINGS", settings(UPDATE_WAIT_SECONDS=5))
        r = runner(server, m)

        async def drive():
            for _ in range(100):
                ended, _ = await r.run_once()
                if ended:
                    return
                await asyncio.sleep(0.02)
        async with http(m) as c:
            resp, _ = await asyncio.gather(
                c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true"}), drive())
        body = resp.json()
        assert resp.status_code == 200 and body["status"] == "unchanged" and body["job_state"] == "done"
        assert body["ontology_id"] == "flopo"

    async def test_batch_wait_reports_every_id(self, m, server, monkeypatch):
        pipeline = FakePipeline(monkeypatch)
        pipeline.outcomes["po"] = outcome("updated")
        monkeypatch.setattr(m, "UPDATE_SETTINGS", settings(UPDATE_WAIT_SECONDS=5))
        r = runner(server, m)

        async def drive():
            for _ in range(100):
                if (await r.run_once())[0]:
                    return
                await asyncio.sleep(0.02)
        async with http(m) as c:
            resp, _ = await asyncio.gather(
                c.post("/admin/check_updates", params={"ontologies": "flopo,po,nosrc,nope", "wait": "true"}),
                drive())
        body = resp.json()
        assert body["status"] == "update_check_complete" and body["requested"] == ["flopo", "po", "nosrc", "nope"]
        assert body["unchanged"] == ["flopo"] and body["updated"] == ["po"]
        assert body["skipped"] == {"nope": "not_registered", "nosrc": "no_source_url"} and body["not_run"] == []

    async def test_running_job_guards_the_ontology_and_the_api_stays_responsive(self, m, server, monkeypatch):
        pipeline = FakePipeline(monkeypatch)
        pipeline.gate = asyncio.Event()
        r = runner(server, m)
        async with http(m) as c:
            job_id = (await c.post("/admin/ontology/flopo/trigger_update")).json()["job_id"]
            task = asyncio.ensure_future(r.run_once())
            await asyncio.wait_for(pipeline.started.wait(), 5)
            status = await asyncio.wait_for(c.get("/admin/update_jobs"), 2)
            assert status.json()["active"]["job"] == job_id
            for method, path, body in (
                    ("PUT", "/admin/ontology/flopo/source", {"source_url": "https://example.org/f.owl"}),
                    ("POST", "/admin/ontology/flopo/disable", None),
                    ("POST", "/admin/ontology/flopo/resolve_update", {"txn_id": "x", "reconciled": True})):
                resp = await asyncio.wait_for(c.request(method, path, json=body), 2)
                assert resp.status_code == 409 and job_id in resp.json()["detail"], (path, resp.text)
            view = (await c.get("/admin/ontology/flopo/update_status")).json()
            assert view["in_progress"] is True and view["update_job"]["job"] == job_id
            assert (await c.post("/admin/ontology/po/disable")).status_code == 200
            queued = await asyncio.wait_for(c.post("/admin/ontology/po/trigger_update"), 2)
            assert queued.status_code == 202
            pipeline.gate.set()
            await asyncio.wait_for(task, 5)
        assert pipeline.ids() == ["flopo"]

    async def test_held_job_recovery(self, m, server, monkeypatch):
        pipeline = FakePipeline(monkeypatch)

        async def unknown(c):
            return await recovery_txn(c, "flopo", txn="txn-9")
        pipeline.outcomes["flopo"] = unknown

        async def once(server_url, task_id):
            return "unknown_task"
        monkeypatch.setattr(updater, "task_status_once", once)
        r = runner(server, m)
        async with http(m) as c:
            job_id = (await c.post("/admin/ontology/flopo/trigger_update")).json()["job_id"]
            await c.post("/admin/ontology/po/trigger_update")
            await r.run_once()
            assert await r.run_once() == (None, True)
            status = (await c.get("/admin/update_jobs")).json()
            claim = status["active"]["claim"]
            assert status["active"]["state"] == "recovery_required"
            url = f"/admin/update_jobs/{job_id}/recover"
            assert (await c.post(url, json={"claim": claim, "reconciled": True})).status_code == 422
            assert (await c.post(url, json={"claim": "1:other", "reconciled": True, "note": "n"})).status_code == 409
            blocked = await c.post(url, json={"claim": claim, "reconciled": True, "note": "n"})
            assert blocked.status_code == 409 and blocked.json()["detail"]["unresolved"][0]["txn_id"] == "txn-9"
            resolved = await c.post("/admin/ontology/flopo/resolve_update",
                                    json={"txn_id": "txn-9", "reconciled": True, "note": "swap had failed"})
            assert resolved.status_code == 200
            released = await c.post(url, json={"claim": claim, "reconciled": True, "note": "txn-9 resolved"})
            assert released.status_code == 200 and released.json()["status"] == "released"
            ended, _ = await r.run_once()
            assert ended["ontologies"] == ["po"] and ended["state"] == "done"
            text = (await c.get(f"/admin/update_jobs/{job_id}")).text + (await c.get("/admin/update_jobs")).text
        assert SECRET not in text and "secret_key" not in text


class TestGuardedChanges:
    """B2: source, disable and enable check the job guard and write in one
    transaction, and queued jobs block them too."""

    async def test_claim_between_the_endpoint_and_the_write_is_refused(self, m, monkeypatch):
        """The reproduction from review (test_guard_race.py), kept: a runner
        queues and claims after the endpoint's own checks."""
        original = m._change_entry
        other = queue(m)
        claimed = []

        async def claim_then_change(oid, change, guard="jobs"):
            await other.enqueue("update", [oid])
            claimed.append((await other.claim("competing-runner"))[0])
            return await original(oid, change, guard)
        monkeypatch.setattr(m, "_change_entry", claim_then_change)
        async with http(m) as c:
            r = await c.put("/admin/ontology/flopo/source", json={"source_url": "https://example.org/changed.owl"})
        assert claimed[0] is not None
        assert r.status_code == 409, r.text
        assert (await registry(m.redis_client, "flopo"))["source_url"] == entry("flopo")["source_url"]

    @pytest.mark.parametrize("method,path,body", [
        ("PUT", "/admin/ontology/flopo/source", {"source_url": "https://example.org/changed.owl"}),
        ("POST", "/admin/ontology/flopo/disable", None),
        ("POST", "/admin/ontology/flopo/enable", None),
    ])
    async def test_claim_inside_the_transaction_is_refused(self, m, server, method, path, body):
        runner_side = update_jobs.JobQueue(make_client(server), m.UPDATE_SETTINGS)

        async def queue_and_claim():
            await runner_side.enqueue("update", ["flopo"])
            await runner_side.claim("r1")
        before = await registry(m.redis_client, "flopo")
        m.redis_client = Interleaved(m.redis_client, update_jobs.REGISTRY_KEY, queue_and_claim)
        async with http(m) as c:
            r = await c.request(method, path, json=body)
        assert r.status_code == 409 and m.redis_client.fired == 1, r.text
        assert await registry(m.redis_client, "flopo") == before

    async def test_a_queued_job_blocks_until_cancelled(self, m):
        async with http(m) as c:
            job_id = (await c.post("/admin/ontology/flopo/trigger_update")).json()["job_id"]
            for method, path, body in (
                    ("PUT", "/admin/ontology/flopo/source", {"source_url": "https://example.org/f.owl"}),
                    ("POST", "/admin/ontology/flopo/disable", None),
                    ("POST", "/admin/ontology/flopo/enable", None)):
                r = await c.request(method, path, json=body)
                assert r.status_code == 409 and job_id in r.json()["detail"], r.text
            assert (await c.post("/admin/ontology/po/disable")).status_code == 200
            assert (await c.post(f"/admin/update_jobs/{job_id}/cancel")).status_code == 200
            assert (await c.post("/admin/ontology/flopo/disable")).status_code == 200
        assert (await registry(m.redis_client, "flopo"))["update_status"] == "disabled"


class _Stats:
    status = 200

    async def json(self):
        return {"status": "classified", "class_count": 120}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _StatsSession:
    def get(self, *a, **k):
        return _Stats()


class TestApiWritersKeepRunnerWrites:
    """Coordinator concern 5: in queued mode a runner may write an entry
    between an API writer's read and its write. Each writer below has that
    write land exactly there (Interleaved), and must keep it."""

    @pytest.fixture
    def racing(self, m, server):
        other = make_client(server)
        m.redis_client = Interleaved(m.redis_client, update_jobs.REGISTRY_KEY, lambda: write_txn(other, "flopo"))
        return m

    async def assert_kept(self, m):
        e = await registry(m.redis_client, "flopo")
        assert m.redis_client.fired == 1
        assert e["update_txn"]["id"] == "txn-runner" and e["source_md5"] == "md5-from-runner"
        return e

    async def test_status_poll(self, racing):
        snapshot = await registry(racing.redis_client, "flopo")
        await racing.fetch_and_update_server_metadata(snapshot, session=_StatsSession())
        e = await self.assert_kept(racing)
        assert e["class_count"] == 120 and e["status"] == "online" and e["reasoner_status"] == "classified"

    async def test_source_sync(self, racing):
        assert await racing._upsert_registry_from_source("flopo", {"source": "manual", "title": "FLOPO"},
                                                         overwrite_source=True)
        assert (await self.assert_kept(racing))["title"] == "FLOPO"

    async def test_metadata_fallbacks(self, racing, monkeypatch):
        monkeypatch.setattr(racing, "_apply_registry_fallbacks", lambda e: e.update(title="Fallback title"))
        monkeypatch.setattr(racing, "_write_servers_to_file", AsyncMockless())
        await racing._reapply_registry_fallbacks_to_all()
        assert (await self.assert_kept(racing))["title"] == "Fallback title"

    async def test_registration(self, racing, monkeypatch):
        monkeypatch.setattr(racing, "_write_servers_to_file", AsyncMockless())
        monkeypatch.setattr(racing, "fetch_and_update_server_metadata", AsyncMockless())
        async with http(racing, auth=None) as c:
            r = await c.post("/register", json={"ontology": "flopo", "url": "http://worker-2:8080/",
                                                 "secret_key": SECRET})
        assert r.status_code == 200 and "secret_key" not in r.json()
        assert (await self.assert_kept(racing))["url"] == "http://worker-2:8080/"

    async def test_registration_hijack_is_still_refused(self, racing, monkeypatch):
        monkeypatch.setattr(racing, "_write_servers_to_file", AsyncMockless())
        async with http(racing, auth=None) as c:
            r = await c.post("/register", json={"ontology": "flopo", "url": "http://evil:8080/",
                                                 "secret_key": "wrong"})
        assert r.status_code == 403
        assert (await registry(racing.redis_client, "flopo"))["url"] == entry("flopo")["url"]

    async def test_registry_unification(self, racing):
        await racing.redis_client.hset("ontology_registry", "flopo", json.dumps({"homepage": "https://x.example"}))
        await racing._migrate_unify_registries()
        assert (await self.assert_kept(racing))["homepage"] == "https://x.example"

    async def test_resolution_keeps_a_concurrent_status_write(self, m, server, monkeypatch):
        e = await registry(m.redis_client, "flopo")
        e["update_txn"] = {"id": "txn-5", "state": "recovery_required", "phase": "index"}
        await m.redis_client.hset(update_jobs.REGISTRY_KEY, "flopo", json.dumps(e))
        other = make_client(server)

        async def poll_write():
            current = await registry(other, "flopo")
            current["class_count"] = 777
            await other.hset(update_jobs.REGISTRY_KEY, "flopo", json.dumps(current))
        m.redis_client = Interleaved(m.redis_client, update_jobs.REGISTRY_KEY, poll_write)
        async with http(m) as c:
            r = await c.post("/admin/ontology/flopo/resolve_update",
                             json={"txn_id": "txn-5", "reconciled": True, "note": "checked"})
        final = await registry(m.redis_client, "flopo")
        assert r.status_code == 200 and m.redis_client.fired == 1
        assert "update_txn" not in final and final["class_count"] == 777
        assert final["update_status"] == "resolved_by_operator"

    async def test_obsolete_callback_is_refused(self, m):
        before = await registry(m.redis_client, "flopo")
        async with http(m, auth=None) as c:
            r = await c.post("/api/update_result", json={"ontology_id": "flopo", "task_id": "t", "status": "success"})
        assert r.status_code == 409 and await registry(m.redis_client, "flopo") == before


class AsyncMockless:
    """An awaitable stand-in that does nothing."""

    async def __call__(self, *a, **k):
        return None


class TestInline:

    @pytest.fixture
    def inline(self, m, monkeypatch):
        monkeypatch.setattr(m, "UPDATE_SETTINGS", settings(mode="inline"))
        return m

    async def test_one_heavy_job_across_every_trigger(self, inline, monkeypatch):
        m = inline
        pipeline = FakePipeline(monkeypatch)
        pipeline.gate = asyncio.Event()
        async with http(m) as c:
            first = await c.post("/admin/ontology/flopo/trigger_update")
            assert first.status_code == 200 and first.json()["status"] == "update_triggered"
            await asyncio.wait_for(pipeline.started.wait(), 5)
            refused = [await c.post("/admin/ontology/po/trigger_update", params={"wait": "true"}),
                       await c.post("/admin/ontology/po/reindex"),
                       await c.post("/admin/check_updates", params={"ontologies": "po"})]
            async with http(m, auth=None) as w:
                refused.append(await w.post("/api/webhook/update/po", headers={"X-Webhook-Secret": SECRET}))
            assert [x.status_code for x in refused] == [409, 409, 409, 409]
            with pytest.raises(update_jobs.QueueError):
                await m._run_update("po")
            pipeline.gate.set()
            for _ in range(100):
                if m._inline_gate.job is None:
                    break
                await asyncio.sleep(0.01)
            again = await c.post("/admin/ontology/po/trigger_update", params={"wait": "true"})
        assert again.status_code == 200 and again.json()["status"] == "unchanged"
        assert pipeline.ids() == ["flopo", "po"] and pipeline.peak == 1

    async def test_batch_is_sequential(self, inline, monkeypatch):
        pipeline = FakePipeline(monkeypatch)
        async with http(inline) as c:
            r = await c.post("/admin/check_updates", params={"ontologies": "flopo,po", "wait": "true"})
        assert r.json()["unchanged"] == ["flopo", "po"] and pipeline.peak == 1

    async def test_unknown_outcome_holds_until_recovered(self, inline, monkeypatch):
        m = inline
        pipeline = FakePipeline(monkeypatch)

        async def unknown(c):
            return await recovery_txn(c, "flopo", txn="txn-3")
        pipeline.outcomes["flopo"] = unknown

        async def once(server_url, task_id):
            return "unknown_task"
        monkeypatch.setattr(updater, "task_status_once", once)
        async with http(m) as c:
            r = await c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true"})
            assert r.json()["recovery_required"] is True
            blocked = await c.post("/admin/ontology/po/trigger_update", params={"wait": "true"})
            assert blocked.status_code == 409 and "recover" in blocked.json()["detail"]
            marker = (await c.get("/admin/update_jobs")).json()["inline"]
            assert marker["state"] == "recovery_required" and marker["held_by"] == "flopo"
            url = f"/admin/update_jobs/{marker['job']}/recover"
            body = {"claim": marker["claim"], "reconciled": True, "note": "checked"}
            assert (await c.post(url, json=body)).status_code == 409
            assert (await c.post("/admin/ontology/flopo/resolve_update",
                                 json={"txn_id": "txn-3", "reconciled": True, "note": "n"})).status_code == 200
            assert (await c.post(url, json=body)).status_code == 200
            again = await c.post("/admin/ontology/po/trigger_update", params={"wait": "true"})
        assert again.status_code == 200 and pipeline.ids() == ["flopo", "po"]

    async def test_refused_beside_a_runner(self, inline, monkeypatch):
        m = inline
        pipeline = FakePipeline(monkeypatch)
        await queue(m).heartbeat("runner-elsewhere", {"runner": "runner-elsewhere"})
        async with http(m) as c:
            r = await c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true"})
        assert r.status_code == 409 and "prohibited" in r.json()["detail"]
        assert pipeline.calls == [] and m._inline_gate.job is None
