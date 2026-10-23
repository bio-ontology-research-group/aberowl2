"""
Ontology updates when central and the worker are on different hosts.

Central and the worker's host have separate ontology directories here, as
in a deployment where each host keeps its own local copy and the worker
mounts its host's copy read-only at /data. The worker fake reads only its
host's directory, so a release reaches it only if the transfer service put
it there. The transfer service is the real one, on a loopback port; the
worker servlets, the source and Elasticsearch are the fakes of
test_update_pipeline.py.
"""

import errno
import json
import os
import shutil
import socket
from types import SimpleNamespace

import pytest

from tests.test_update_pipeline import (EXPORT, NEW_INDEX, SERVED, SOURCE, STAGING, V1, WORKER, FakeES,  # noqa: F401
                                        FakeRedis, FakeWorker, assert_clean_end, assert_recovery, flopo_entry, md5,
                                        po_entry)
from tests.transfer_helpers import (NEW, OLD, TOKEN, ServiceThread, aberowl_transfer, make_tls_files, real,
                                    service_config, sha, snapshot, worker_host)
from app.intake import transfer, update_jobs, updater  # noqa: E402  (on sys.path through test_update_pipeline)

pytestmark = pytest.mark.unit

HAND = b"<rdf:RDF><!-- edited by hand on the worker host --></rdf:RDF>\n"
NEWER = b"<rdf:RDF><!-- flopo release 3 --></rdf:RDF>\n"


def replace_file(path, data):
    tmp = path.with_name("." + path.name + ".by-hand")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def no_links(src, dst):
    """A file system that refuses hard links (EXDEV, or protected_hardlinks)."""
    raise OSError(errno.EXDEV, "Invalid cross-device link")


def count_requests(monkeypatch, suffix):
    """The HTTP statuses of the transfer requests whose path ends in suffix."""
    real_request = transfer.RemoteTransfer._request
    statuses = []

    async def counting(self, method, path, **kw):
        answer = await real_request(self, method, path, **kw)
        if path.endswith(suffix):
            statuses.append(answer[0])
        return answer
    monkeypatch.setattr(transfer.RemoteTransfer, "_request", counting)
    return statuses


def lose_answers(monkeypatch, suffix, times=1):
    """The request reaches the service; its answer does not reach central."""
    real_request = transfer.RemoteTransfer._request
    lost = []

    async def lossy(self, method, path, **kw):
        answer = await real_request(self, method, path, **kw)
        if path.endswith(suffix) and len(lost) < times:
            lost.append(answer[0])
            raise transfer.NoAnswer(f"{method} {path}: answer lost")
        return answer
    monkeypatch.setattr(transfer.RemoteTransfer, "_request", lossy)
    return lost


class SplitWorker(FakeWorker):
    """A worker that sees only its own host's directory, read-only, at /data,
    and records which file it loaded each ontology from."""

    def __init__(self, es, root):
        super().__init__(es)
        self.root = root
        self.loaded = {}
        self.recorded = {}
        self.validated = None
        self.on_hotswap = None

    def host_path(self, path):
        assert path.startswith("/data/") and ".." not in path, path
        return self.root / path[len("/data/"):]

    async def worker_served_path(self, server_url, oid):
        self.events.append(("served", oid))
        if self.served_state != "ok":
            return {"state": self.served_state, "message": "down"}
        return {"state": "ok", "path": self.recorded.get(oid, f"/data/{oid}/{oid}.owl"), "status": "classified"}

    async def validate(self, server_url, path, oid=None, secret_key="", served_path=None, export_id=None):
        if not self.host_path(path).is_file():
            self.events.append(("validate", path, oid, served_path, export_id))
            return {"ok": False, "answered": True,
                    "message": f"owlPath must be an existing regular file under /data: {path}"}
        self.validated = self.host_path(path).read_bytes()
        return await super().validate(server_url, path, oid, secret_key, served_path, export_id)

    async def start_hotswap(self, server_url, secret_key, oid, path, served_path=None, reasoner_type=None):
        started = await super().start_hotswap(server_url, secret_key, oid, path, served_path, reasoner_type)
        if started["state"] == "started":
            self.loaded[oid] = self.host_path(path).read_bytes()
            self.recorded[oid] = path
        if self.on_hotswap:
            self.on_hotswap()
        return started

    async def finalize(self, server_url, secret_key, oid, staged, served):
        result = await super().finalize(server_url, secret_key, oid, staged, served)
        if result["ok"]:
            assert self.recorded.get(oid) == staged
            assert self.host_path(served).read_bytes() == self.loaded[oid], "finalize names a file with what was loaded"
            self.recorded[oid] = served
        return result

    def restart(self, oid):
        """What a restart loads: the recorded path, from the read-only mount."""
        return self.host_path(self.recorded.get(oid, f"/data/{oid}/{oid}.owl")).read_bytes()


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    import itertools
    monkeypatch.setattr(updater, "_locks", {})
    monkeypatch.setattr(updater, "_worker_locks", {})
    ids = (f"txn-{i}" for i in itertools.count(1))
    monkeypatch.setattr(updater, "new_txn_id", lambda: next(ids))
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)
    monkeypatch.delenv("UPDATE_TRANSFER_CONFIG", raising=False)


@pytest.fixture
def hosts(tmp_path):
    base = real(tmp_path)
    central = base / "central"
    for oid in ("flopo", "po"):
        (central / oid).mkdir(parents=True)
        (central / oid / f"{oid}.owl").write_bytes(OLD)
    return SimpleNamespace(central=central, worker=worker_host(base / "worker-host"),
                           state=base / "transfer-state", base=base)


@pytest.fixture
def service(hosts):
    with ServiceThread(aberowl_transfer.create_app(service_config(hosts.worker, hosts.state))) as svc:
        yield svc


def write_mapping(hosts, monkeypatch, transfers):
    cfg = hosts.base / "transfers.json"
    cfg.write_text(json.dumps(transfers if isinstance(transfers, dict) else {"transfers": transfers}))
    monkeypatch.setenv("UPDATE_TRANSFER_CONFIG", str(cfg))
    return cfg


def token_file(hosts, token=TOKEN):
    path = hosts.base / "transfer-token"
    path.write_text(token + "\n")
    return str(path)


@pytest.fixture
def mapping(hosts, service, monkeypatch):
    return write_mapping(hosts, monkeypatch, [{
        "worker_url": WORKER + "/", "transfer_url": service.url, "token_file": token_file(hosts),
        "allow_insecure_http": True}])


@pytest.fixture
def es():
    return FakeES()


@pytest.fixture
def worker(monkeypatch, es, hosts):
    w = SplitWorker(es, hosts.worker)
    for name, fake in {
        "worker_served_path": w.worker_served_path, "check_version": w.check_version,
        "download_ontology": w.download, "validate_staged": w.validate, "discard_export": w.discard,
        "start_indexing": w.start_indexing, "start_hotswap": w.start_hotswap, "poll_task": w.poll_task,
        "finalize_served_path": w.finalize, "task_status_once": w.task_status_once,
    }.items():
        monkeypatch.setattr(updater, name, fake)
    return w


async def run(hosts, redis, es, oid="flopo", **kw):
    return await updater.execute_update_pipeline(
        ontology_id=oid, registry_entry=redis.entry(oid), redis_client=redis, es_mgr=es,
        ontologies_base_path=str(hosts.central), es_url="http://es:9200", **kw)


def journal(hosts, txn="txn-1", oid="flopo"):
    path = hosts.state / "journal" / oid / f"{txn}.json"
    return json.loads(path.read_text()) if path.exists() else None


def on_host(hosts, rel):
    return (hosts.worker / rel).read_bytes()


def changed(before, after):
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


# ---------------------------------------------------------------------------
# A complete update across hosts
# ---------------------------------------------------------------------------

class TestRemoteUpdate:

    async def test_the_release_reaches_the_worker_host_and_survives_a_restart(self, hosts, mapping, worker, es):
        worker_before, central_before = snapshot(hosts.worker), snapshot(hosts.central)
        redis = FakeRedis({"flopo": flopo_entry(), "po": po_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result

        # The worker validated and loaded the release from its own host.
        assert worker.validated == NEW and worker.loaded["flopo"] == NEW
        assert worker.events[3:] == [
            ("validate", STAGING, "flopo", SERVED, "txn-1"),
            ("index", EXPORT, NEW_INDEX),
            ("poll", "idx-1"),
            ("hotswap", STAGING, SERVED),
            ("poll", "swap-2"),
            ("finalize", STAGING, SERVED),
        ]
        # A restart reads the canonical path, which holds the release.
        assert worker.recorded["flopo"] == SERVED and worker.restart("flopo") == NEW

        # On the worker host: the served file and its archive, nothing else.
        after = snapshot(hosts.worker)
        assert changed(worker_before, after) == {"flopo/flopo.owl", "flopo/archive/flopo_txn-1.owl"}
        assert on_host(hosts, "flopo/archive/flopo_txn-1.owl") == OLD
        assert journal(hosts)["state"] == "finalized"

        # On central: its own copy follows; it never staged under the ontology.
        assert changed(central_before, snapshot(hosts.central)) == {"flopo/flopo.owl"}
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == NEW
        assert not (hosts.central / ".update-downloads" / "flopo").exists()

        e = redis.entry("flopo")
        assert e["source_md5"] == md5(NEW) and e["source_etag"] == '"v2"'
        assert e["archived_owl_path"] == "/data/flopo/archive/flopo_txn-1.owl"
        assert e["update_history"][-1]["transport"] == "remote"
        assert e["update_history"][-1]["sha256"] == sha(NEW)
        assert es.aliases["flopo"] == NEW_INDEX and V1 in es.indices, "the previous index is kept"
        assert "update_warnings" not in e
        assert_clean_end(redis)
        assert redis.entry("po") == po_entry()

    async def test_without_a_mapping_the_worker_host_never_sees_the_release(self, hosts, worker, es):
        """The shared-directory default, unchanged: on split hosts it fails
        validation safely and changes nothing on the worker's host."""
        before = snapshot(hosts.worker)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["error"] == "owl_validation_failed" and "existing regular file" in result["message"]
        assert snapshot(hosts.worker) == before
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == OLD
        assert not (hosts.central / "flopo" / "updates" / "txn-1").exists()
        assert_clean_end(redis)

    async def test_dependents_are_read_from_the_worker_host(self, hosts, mapping, worker, es):
        assert not (hosts.central / "import_catalogues").exists()
        redis = FakeRedis({"po": po_entry(), "flopo": flopo_entry()})
        result = await run(hosts, redis, es, oid="po")
        assert result["status"] == "updated" and result["dependents"] == ["flopo"]
        assert "po" in redis.entry("flopo")["stale_imports"]

    async def test_reindex_needs_no_transfer(self, hosts, mapping, worker, es):
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await updater.execute_reindex("flopo", redis.entry("flopo"), redis, es, str(hosts.central), "")
        assert result["success"] and ("index", "/tmp/exports/txn-1/flopo.owl", NEW_INDEX) in worker.events
        assert ("validate", SERVED, "flopo", SERVED, "txn-1") in worker.events
        assert not (hosts.state / "journal" / "flopo").exists()


# ---------------------------------------------------------------------------
# Change detection uses the worker host's file
# ---------------------------------------------------------------------------

class TestDetection:

    async def test_markers_are_not_trusted_when_the_host_serves_another_file(self, hosts, mapping, worker, es):
        """The registry and central's copy say NEW was installed; the worker
        host still serves OLD. An unchanged HEAD must not end the run."""
        (hosts.central / "flopo" / "flopo.owl").write_bytes(NEW)
        worker.version.update(changed=False, etag='"v2"')
        redis = FakeRedis({"flopo": flopo_entry(source_etag='"v2"', source_md5=md5(NEW))})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated" and "download" in worker.kinds()
        assert on_host(hosts, "flopo/flopo.owl") == NEW

    async def test_a_host_serving_the_release_still_gets_a_full_update_if_the_registry_disagrees(
            self, hosts, mapping, worker, es):
        """The registry's last complete update is OLD; the host serves NEW
        (an update that stopped after promotion, or a hand copy). Identical
        bytes are not "unchanged": the full update runs, so search follows."""
        (hosts.worker / "flopo" / "flopo.owl").write_bytes(NEW)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert es.aliases["flopo"] == NEW_INDEX and ("hotswap", STAGING, SERVED) in worker.events
        assert on_host(hosts, "flopo/flopo.owl") == NEW and journal(hosts)["sha256"] == journal(hosts)["base_sha256"]
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == NEW
        assert redis.entry("flopo")["source_md5"] == md5(NEW)

    async def test_unchanged_when_host_and_registry_agree_and_central_copy_is_repaired(
            self, hosts, mapping, worker, es):
        (hosts.worker / "flopo" / "flopo.owl").write_bytes(NEW)
        before = snapshot(hosts.worker)
        redis = FakeRedis({"flopo": flopo_entry(source_md5=md5(NEW))})
        result = await run(hosts, redis, es)
        assert result["status"] == "unchanged" and "warnings" not in result
        assert "validate" not in worker.kinds() and es.calls == []
        assert snapshot(hosts.worker) == before and not (hosts.state / "journal" / "flopo").exists()
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == NEW, "central's copy follows"
        assert redis.entry("flopo")["source_md5"] == md5(NEW)
        assert_clean_end(redis)

    async def test_an_unchanged_head_does_not_hide_a_stale_central_copy(self, hosts, mapping, worker, es):
        (hosts.worker / "flopo" / "flopo.owl").write_bytes(NEW)
        worker.version.update(changed=False, etag='"v2"')
        redis = FakeRedis({"flopo": flopo_entry(source_etag='"v2"', source_md5=md5(NEW))})
        result = await run(hosts, redis, es)
        assert result["status"] == "unchanged" and "download" in worker.kinds()
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == NEW

    async def test_a_current_central_copy_needs_no_download(self, hosts, mapping, worker, es):
        for root in (hosts.worker, hosts.central):
            (root / "flopo" / "flopo.owl").write_bytes(NEW)
        worker.version.update(changed=False, etag='"v2"')
        redis = FakeRedis({"flopo": flopo_entry(source_etag='"v2"', source_md5=md5(NEW))})
        result = await run(hosts, redis, es)
        assert result["status"] == "unchanged" and worker.kinds() == ["served", "head"]

    async def test_a_failed_central_copy_refresh_is_reported_and_retried(self, hosts, mapping, worker, es,
                                                                         monkeypatch):
        real_refresh = updater._refresh_central_copy
        monkeypatch.setattr(updater, "_refresh_central_copy",
                            lambda paths, download, md5=None: "central's copy is read-only; the next check retries")
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated" and result["warnings"]
        assert redis.entry("flopo")["update_warnings"] == result["warnings"]
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == OLD

        monkeypatch.setattr(updater, "_refresh_central_copy", real_refresh)
        worker.version.update(changed=False)   # the source has not changed since
        again = await run(hosts, redis, es)
        assert again["status"] == "unchanged" and "download" in worker.kinds()
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == NEW
        assert "update_warnings" not in redis.entry("flopo")

    @pytest.mark.parametrize("history,expected", [
        ([], "unchanged"),                                         # a source configured for the first time
        ([{"status": "recovery_required"}, {"status": "resolved_by_operator"}], "updated"),
    ])
    async def test_without_a_recorded_md5_only_a_clean_history_is_a_baseline(
            self, hosts, mapping, worker, es, history, expected):
        (hosts.worker / "flopo" / "flopo.owl").write_bytes(NEW)
        redis = FakeRedis({"flopo": flopo_entry(source_md5=None, source_etag=None, update_history=history)})
        result = await run(hosts, redis, es)
        assert result["status"] == expected, result
        assert redis.entry("flopo")["source_md5"] == md5(NEW)


# ---------------------------------------------------------------------------
# Failures with a known state: the remote stage is removed
# ---------------------------------------------------------------------------

class TestKnownFailures:

    async def test_validation_failure_removes_the_remote_stage(self, hosts, mapping, worker, es):
        worker.validation = {"ok": False, "answered": True, "message": "Imports of flopo: not listed"}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["error"] == "owl_validation_failed"
        assert worker.validated == NEW, "the worker read the transferred release"
        assert journal(hosts)["state"] == "aborted"
        assert not (hosts.worker / "flopo" / "updates").exists()
        assert on_host(hosts, "flopo/flopo.owl") == OLD
        assert_clean_end(redis)

    async def test_a_host_file_changed_before_the_transfer_fails_cleanly(self, hosts, mapping, worker, es, monkeypatch):
        real_download = worker.download

        async def download_then_edit(*args, **kw):
            result = await real_download(*args, **kw)
            (hosts.worker / "flopo" / "flopo.owl").write_bytes(HAND)
            return result
        monkeypatch.setattr(updater, "download_ontology", download_then_edit)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["error"] == "transfer_failed" and "base_mismatch" in result["message"]
        assert "validate" not in worker.kinds()
        assert on_host(hosts, "flopo/flopo.owl") == HAND and journal(hosts) is None
        assert not (hosts.worker / "flopo" / "updates").exists()
        assert not (hosts.central / ".update-downloads" / "flopo").exists()
        assert_clean_end(redis)

    async def test_an_unpermitted_ontology_is_refused_by_the_service(self, hosts, mapping, worker, es):
        before = snapshot(hosts.worker)
        redis = FakeRedis({"pato": flopo_entry(ontology="pato", ontology_id="pato")})
        result = await run(hosts, redis, es, oid="pato")
        assert result["status"] == "skipped" and result["error"] == "transfer_refused"
        assert "download" not in worker.kinds() and snapshot(hosts.worker) == before


# ---------------------------------------------------------------------------
# Unknown outcomes: recovery, the remote stage is kept
# ---------------------------------------------------------------------------

class TestUnknownOutcomes:

    async def test_unknown_validation_keeps_the_remote_stage(self, hosts, mapping, worker, es):
        worker.validation = {"ok": False, "answered": False, "message": "no answer"}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["recovery_required"]
        txn = assert_recovery(redis, "validation_unknown", phase="validate")
        assert txn["transport"] == "remote" and txn["transfer"].startswith("http://127.0.0.1:")
        assert txn["base_sha256"] == sha(OLD) and txn["sha256"] == sha(NEW) and txn["staging"] == STAGING
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW and journal(hosts)["state"] == "staged"
        assert on_host(hosts, "flopo/flopo.owl") == OLD
        again = await run(hosts, redis, es)
        assert again["error"] == "recovery_required" and journal(hosts, "txn-2") is None

    async def test_hotswap_timeout_keeps_the_remote_stage(self, hosts, mapping, worker, es):
        worker.swaps = [{"status": "timeout", "message": "no result after 1800s", "data": {}}]
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(hosts, redis, es)
        assert_recovery(redis, "hotswap_unknown", phase="hotswap")
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW, "the worker may be reading it"
        assert on_host(hosts, "flopo/flopo.owl") == OLD and journal(hosts)["state"] == "staged"

    async def test_failed_finalize_keeps_the_promoted_stage(self, hosts, mapping, worker, es):
        worker.finalize_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(hosts, redis, es)
        assert_recovery(redis, "finalize_failed", phase="finalize")
        assert on_host(hosts, "flopo/flopo.owl") == NEW
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW, "the worker still records the stage"
        assert journal(hosts)["state"] == "promoted"
        assert redis.entry("flopo")["source_etag"] == '"v1"', "markers move only with a complete update"
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == OLD

    async def test_a_host_file_changed_during_the_run_is_never_overwritten(self, hosts, mapping, worker, es):
        worker.on_hotswap = lambda: (hosts.worker / "flopo" / "flopo.owl").write_bytes(HAND)
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(hosts, redis, es)
        txn = assert_recovery(redis, "promote_failed", phase="promote")
        assert "served_changed" in txn["error"]
        assert on_host(hosts, "flopo/flopo.owl") == HAND
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW
        assert not (hosts.worker / "flopo" / "archive").exists() and "finalize" not in worker.kinds()
        assert es.aliases["flopo"] == V1

    async def test_an_unreachable_service_during_the_transfer_needs_recovery(
            self, hosts, mapping, worker, es, monkeypatch):
        real_request = transfer.RemoteTransfer._request

        async def down_after_prepare(self, method, path, **kw):
            if path.endswith("/file") or (method == "GET" and "/transactions/" in path):
                raise transfer.NoAnswer(f"{method} {path}: connection reset")
            return await real_request(self, method, path, **kw)
        monkeypatch.setattr(transfer.RemoteTransfer, "_request", down_after_prepare)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, "transfer_unknown", phase="transfer")
        assert "validate" not in worker.kinds()
        assert on_host(hosts, "flopo/flopo.owl") == OLD and journal(hosts)["state"] == "prepared"

    async def test_an_unreachable_service_is_a_skip_before_any_download(self, hosts, worker, es, monkeypatch):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{sock.getsockname()[1]}"
        sock.close()
        write_mapping(hosts, monkeypatch, [{"worker_url": WORKER, "transfer_url": closed,
                                            "token_file": token_file(hosts), "allow_insecure_http": True}])
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "skipped" and result["error"] == "transfer_unreachable"
        assert worker.kinds() == ["served"]
        assert redis.entry("flopo")["last_check_status"] == "skipped"
        assert_clean_end(redis)


# ---------------------------------------------------------------------------
# Lost answers and interrupted promotions are reconciled from the files
# ---------------------------------------------------------------------------

class TestReconciliation:

    async def test_a_lost_upload_answer_is_not_sent_again(self, hosts, mapping, worker, es, monkeypatch):
        real_request = transfer.RemoteTransfer._request
        uploads = []

        async def lose_first_upload_answer(self, method, path, **kw):
            status = await real_request(self, method, path, **kw)
            if path.endswith("/file"):
                uploads.append(status[0])
                if len(uploads) == 1:
                    raise transfer.NoAnswer("connection reset after the upload")
            return status
        monkeypatch.setattr(transfer.RemoteTransfer, "_request", lose_first_upload_answer)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert uploads == [201], "the service's record showed the stage; nothing was sent twice"
        assert on_host(hosts, "flopo/flopo.owl") == NEW

    async def test_a_lost_promotion_answer_is_read_back(self, hosts, mapping, worker, es, monkeypatch):
        real_request = transfer.RemoteTransfer._request
        promotions = []

        async def lose_promotion_answer(self, method, path, **kw):
            status = await real_request(self, method, path, **kw)
            if path.endswith("/promote"):
                promotions.append(status[0])
                raise transfer.NoAnswer("timeout")
            return status
        monkeypatch.setattr(transfer.RemoteTransfer, "_request", lose_promotion_answer)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert promotions == [200]
        assert os.listdir(hosts.worker / "flopo" / "archive") == ["flopo_txn-1.owl"]

    async def test_an_interrupted_promotion_is_completed(self, hosts, mapping, worker, es, monkeypatch):
        real_replace = aberowl_transfer._replace
        calls = []

        def fail_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise OSError(5, "Input/output error")
            real_replace(src, dst)
        monkeypatch.setattr(aberowl_transfer, "_replace", fail_once)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert len(calls) == 2 and on_host(hosts, "flopo/flopo.owl") == NEW
        assert os.listdir(hosts.worker / "flopo" / "archive") == ["flopo_txn-1.owl"]

    async def test_a_stage_installed_before_its_journal_entry_completes_the_update(
            self, hosts, mapping, worker, es, monkeypatch):
        real_write = aberowl_transfer.TransferService._write
        failed = []

        def interrupted(self, record):
            if record.get("state") == "staged" and not record.get("settled_after_interruption") and not failed:
                failed.append(record["txn"])
                raise OSError(5, "Input/output error")
            real_write(self, record)
        monkeypatch.setattr(aberowl_transfer.TransferService, "_write", interrupted)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert failed == ["txn-1"] and journal(hosts)["settled_after_interruption"] is True
        assert worker.validated == NEW and on_host(hosts, "flopo/flopo.owl") == NEW

    async def test_identical_release_without_links_and_a_lost_promotion_answer(
            self, hosts, mapping, worker, es, monkeypatch):
        """A forced update with unchanged bytes on a file system without hard
        links; the promotion's answer is lost. Its journal, not the equal
        content, confirms the replace."""
        monkeypatch.setattr(aberowl_transfer, "_link", no_links)
        lose_answers(monkeypatch, "/promote", times=1)
        worker.body = OLD
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es, force=True)
        assert result["status"] == "updated", result
        served = hosts.worker / "flopo" / "flopo.owl"
        assert served.read_bytes() == OLD and journal(hosts)["promote_file"][1] == served.stat().st_ino
        assert on_host(hosts, "flopo/archive/flopo_txn-1.owl") == OLD
        assert es.aliases["flopo"] == NEW_INDEX

    async def test_no_space_to_copy_is_a_refusal_not_an_unknown(self, hosts, mapping, worker, es, monkeypatch):
        promotions = count_requests(monkeypatch, "/promote")
        real_usage = aberowl_transfer.shutil.disk_usage

        def disk_full_from_the_swap():
            monkeypatch.setattr(aberowl_transfer, "_link", no_links)
            monkeypatch.setattr(aberowl_transfer.shutil, "disk_usage",
                                lambda path: real_usage(path)._replace(free=0))
        worker.on_hotswap = disk_full_from_the_swap
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(hosts, redis, es)
        txn = assert_recovery(redis, "promote_failed", phase="promote")
        assert "insufficient_space" in txn["error"] and promotions == [507]
        assert on_host(hosts, "flopo/flopo.owl") == OLD
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW, "the worker serves it"


# ---------------------------------------------------------------------------
# A stage kept after a complete update is removed before the next starts
# ---------------------------------------------------------------------------

class TestKeptStage:

    async def first_run_keeps_the_stage(self, hosts, worker, es, monkeypatch):
        real_release = transfer.RemoteTransfer.release

        async def no_answer(self, txn, sha256):
            return {"ok": False, "message": "no answer"}
        monkeypatch.setattr(transfer.RemoteTransfer, "release", no_answer)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated" and result["warnings"]
        e = redis.entry("flopo")
        assert e["update_warnings"] == result["warnings"]
        assert e["transfer_release_pending"]["txn"] == "txn-1"
        assert e["transfer_release_pending"]["sha256"] == sha(NEW)
        assert journal(hosts)["state"] == "promoted"
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW
        # The next release upstream.
        worker.body = NEWER
        worker.version.update(etag='"v3"')
        worker.events.clear()
        return redis, real_release

    async def test_the_next_update_removes_it_first(self, hosts, mapping, worker, es, monkeypatch):
        redis, real_release = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        monkeypatch.setattr(transfer.RemoteTransfer, "release", real_release)
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert journal(hosts)["state"] == "finalized" and journal(hosts, "txn-2")["state"] == "finalized"
        assert not (hosts.worker / "flopo" / "updates").exists()
        assert on_host(hosts, "flopo/flopo.owl") == NEWER
        e = redis.entry("flopo")
        assert "transfer_release_pending" not in e and "update_warnings" not in e

    async def test_while_it_cannot_be_removed_the_next_update_is_skipped(self, hosts, mapping, worker, es,
                                                                        monkeypatch):
        redis, _ = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        result = await run(hosts, redis, es)
        assert result["status"] == "skipped" and result["error"] == "transfer_cleanup_pending"
        assert "download" not in worker.kinds() and journal(hosts, "txn-2") is None
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW
        assert redis.entry("flopo")["transfer_release_pending"]["txn"] == "txn-1"

    async def test_it_is_not_removed_while_the_worker_records_it(self, hosts, mapping, worker, es, monkeypatch):
        redis, real_release = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        monkeypatch.setattr(transfer.RemoteTransfer, "release", real_release)
        worker.recorded["flopo"] = STAGING     # e.g. a reload from the stage by hand
        result = await run(hosts, redis, es)
        assert result["error"] == "worker_path_mismatch"
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW and journal(hosts)["state"] == "promoted"

    async def test_it_is_not_removed_when_the_host_serves_another_file(self, hosts, mapping, worker, es,
                                                                      monkeypatch):
        redis, real_release = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        monkeypatch.setattr(transfer.RemoteTransfer, "release", real_release)
        # Replaced by hand (the stage is the same file as the served one, so
        # an in-place edit would change it too).
        replace_file(hosts.worker / "flopo" / "flopo.owl", HAND)
        result = await run(hosts, redis, es)
        assert result["error"] == "transfer_cleanup_pending" and "not removed automatically" in result["message"]
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW and journal(hosts)["state"] == "promoted"
        assert "download" not in worker.kinds()


    @pytest.mark.parametrize("rollback_first", [False, True])
    async def test_operator_finalized_cleanup_is_recognized_after_rollback(
            self, hosts, mapping, worker, es, monkeypatch, rollback_first):
        redis, real_release = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        monkeypatch.setattr(transfer.RemoteTransfer, "release", real_release)
        remote = transfer.RemoteTransfer(transfer.target_for(WORKER), "flopo")
        if rollback_first:
            replace_file(hosts.worker / "flopo/flopo.owl", HAND)
        status, _ = await remote._request("POST", "/transactions/txn-1/finalize",
                                          json_body={"sha256": sha(NEW), "served_changed_ok": rollback_first})
        assert status == 200
        if not rollback_first:
            replace_file(hosts.worker / "flopo/flopo.owl", HAND)
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert "transfer_release_pending" not in redis.entry("flopo")
        assert on_host(hosts, "flopo/flopo.owl") == NEWER
        assert journal(hosts, "txn-1")["state"] == "finalized"

    @pytest.mark.parametrize("fault", ["missing", "mapping_changed"])
    async def test_unknown_cleanup_has_supported_operator_recovery(
            self, hosts, mapping, worker, es, monkeypatch, fault):
        redis, real_release = await self.first_run_keeps_the_stage(hosts, worker, es, monkeypatch)
        monkeypatch.setattr(transfer.RemoteTransfer, "release", real_release)
        if fault == "missing":
            (hosts.state / "journal/flopo/txn-1.json").unlink()
        else:
            entry = redis.entry("flopo")
            entry["transfer_release_pending"]["transfer"] = "https://previous-host.example"
            await redis.hset("registered_servers", "flopo", json.dumps(entry))
        result = await run(hosts, redis, es)
        assert result["error"] == "transfer_cleanup_pending"
        assert "clear_transfer_cleanup=true" in result["message"]
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW
        assert not await updater.resolve_transfer_cleanup(redis, "flopo", "wrong", "reconciled")
        assert not await updater.resolve_transfer_cleanup(redis, "flopo", "txn-1", "")
        assert await updater.resolve_transfer_cleanup(redis, "flopo", "txn-1", "operator reconciled original host")
        assert "transfer_release_pending" not in redis.entry("flopo")
        assert redis.entry("flopo")["update_history"][-1]["status"] == "transfer_cleanup_resolved"
        assert on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW, "marker resolution deletes nothing"


# ---------------------------------------------------------------------------
# A run that stopped after promotion is completed by the next one
# ---------------------------------------------------------------------------

class TestRerunAfterRecovery:

    @pytest.mark.parametrize("failure", ["alias", "finalize"])
    async def test_search_and_dependents_follow_after_resolve(self, hosts, mapping, worker, es, failure):
        """The host serves the new release after the first run, but search,
        the registry and dependents do not. After the operator's resolve the
        next run must not call that "unchanged"."""
        if failure == "alias":
            es.swap_ok = False
        else:
            worker.finalize_ok = False
        redis = FakeRedis({"po": po_entry(), "flopo": flopo_entry()})
        await run(hosts, redis, es, oid="po")
        assert_recovery(redis, "alias_swap_failed" if failure == "alias" else "finalize_failed", oid="po")
        assert on_host(hosts, "po/po.owl") == NEW and "stale_imports" not in redis.entry("flopo")

        # The operator reconciles as transfer_service/README.md describes.
        if failure == "finalize":
            worker.recorded["po"] = "/data/po/po.owl"             # finalizeUpdate.groovy
            target = transfer.target_for(WORKER)
            assert (await transfer.RemoteTransfer(target, "po").release("txn-1", sha(NEW)))["ok"]
        assert await updater.resolve_transaction(redis, "po", "txn-1", "reconciled", None)
        es.swap_ok, worker.finalize_ok = True, True

        result = await run(hosts, redis, es, oid="po")
        assert result["status"] == "updated", result
        assert es.aliases["po"] == "aberowl_po_classes_txn-2"
        e = redis.entry("po")
        assert e["active_es_index"] == "aberowl_po_classes_txn-2" and e["source_md5"] == md5(NEW)
        assert e["update_status"] == "ok" and e["catalogue_dependents"] == ["flopo"]
        assert "po" in redis.entry("flopo")["stale_imports"]
        assert worker.restart("po") == NEW and on_host(hosts, "po/po.owl") == NEW
        assert_clean_end(redis, oid="po")

    async def test_an_open_transaction_on_the_host_is_an_actionable_skip(self, hosts, mapping, worker, es):
        worker.finalize_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(hosts, redis, es)
        worker.recorded["flopo"] = SERVED                          # the worker was reconciled ...
        await updater.resolve_transaction(redis, "flopo", "txn-1", "worker reconciled", None)
        worker.finalize_ok = True
        worker.events.clear()
        skipped = await run(hosts, redis, es)                      # ... but not the transfer service
        assert skipped["error"] == "transfer_transaction_open" and "txn-1" in skipped["message"]
        assert "download" not in worker.kinds() and on_host(hosts, "flopo/updates/txn-1/flopo.owl") == NEW

        assert (await transfer.RemoteTransfer(transfer.target_for(WORKER), "flopo").release(
            "txn-1", sha(NEW)))["ok"]
        result = await run(hosts, redis, es)
        assert result["status"] == "updated" and es.aliases["flopo"] == "aberowl_flopo_classes_txn-2"


# ---------------------------------------------------------------------------
# Configuration fails closed
# ---------------------------------------------------------------------------

class TestMapping:

    @pytest.mark.parametrize("entry,raw", [
        ({"transfer_url": "http://transfer.internal.example:8090"}, None),         # plain http, not allowed
        ({"token_file": None, "token_env": "UNSET_TRANSFER_TOKEN_VARIABLE"}, None),
        ({"transfer_url": "https://user:pw@transfer.internal.example"}, None),
        ({"verify": False}, None),
        (None, {"transfers": []}),
        (None, {"transfers": {}, "extra": 1}),
    ])
    async def test_an_unusable_mapping_refuses_every_update(self, hosts, worker, es, monkeypatch, entry, raw):
        if raw is None:
            base = {"worker_url": WORKER, "transfer_url": "https://transfer.internal.example:8443",
                    "token_file": token_file(hosts)}
            base.update(entry)
            raw = {"transfers": [{k: v for k, v in base.items() if v is not None}]}
        write_mapping(hosts, monkeypatch, raw)
        before = snapshot(hosts.worker), snapshot(hosts.central)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "skipped" and result["error"] == "transfer_config_invalid"
        assert worker.events == [] and (snapshot(hosts.worker), snapshot(hosts.central)) == before
        assert TOKEN not in result["message"]
        assert_clean_end(redis)

    def test_a_weak_token_is_refused_without_echoing_it(self, hosts, monkeypatch):
        write_mapping(hosts, monkeypatch, [{"worker_url": WORKER, "transfer_url": "https://t.example",
                                            "token_file": token_file(hosts, "weak-secret")}])
        with pytest.raises(transfer.TransferConfigError) as e:
            transfer.target_for(WORKER)
        assert "weak-secret" not in str(e.value)

    def test_only_mapped_workers_use_the_service(self, hosts, monkeypatch):
        write_mapping(hosts, monkeypatch, [{"worker_url": "HTTP://Worker-1:8080/", "transfer_url": "https://t.example",
                                            "token_env": "TRANSFER_TOKEN_FOR_TEST"}])
        monkeypatch.setenv("TRANSFER_TOKEN_FOR_TEST", TOKEN)
        target = transfer.target_for(WORKER)
        assert target.transfer_url == "https://t.example" and TOKEN not in repr(target)
        assert transfer.target_for("http://worker-2:8080") is None
        monkeypatch.delenv("UPDATE_TRANSFER_CONFIG")
        assert transfer.target_for(WORKER) is None


# ---------------------------------------------------------------------------
# Ownership lost while a remote step was awaited (update runner fencing)
# ---------------------------------------------------------------------------

class LosingRedis(FakeRedis):
    """FakeRedis with the contract of the update runner's fenced client
    (update_jobs.FencedRedis): once the claim is lost, check_claim and
    registry writes raise OwnershipLost."""

    lost = False

    async def check_claim(self):
        if self.lost:
            raise update_jobs.OwnershipLost("the claim no longer holds the global slot")

    async def hset(self, h, k, v):
        if self.lost:
            raise update_jobs.OwnershipLost("registry write refused")
        return await super().hset(h, k, v)


class TestOwnershipLostDuringARemoteStep:
    """The claim is released while the runner waits on the transfer service
    or the worker. Whatever that call did is done; nothing after it may run:
    central's copy, its download and the indices stay as they were, and
    nothing is recorded."""

    def lose_after(self, redis, es, hosts, call, snap):
        async def wrapped(*a, **k):
            result = await call(*a, **k)
            snap.update(entry=redis.entry("flopo"), es_calls=list(es.calls), aliases=dict(es.aliases),
                        central=snapshot(hosts.central))
            redis.lost = True
            return result
        return wrapped

    def assert_nothing_after(self, redis, es, hosts, snap):
        assert redis.entry("flopo") == snap["entry"] and snap["entry"]["update_txn"]["id"] == "txn-1"
        assert es.calls == snap["es_calls"] and es.aliases == snap["aliases"]
        assert snapshot(hosts.central) == snap["central"]
        assert (hosts.central / ".update-downloads" / "flopo" / "txn-1" / "flopo.owl").read_bytes() == NEW

    async def test_after_the_stage_is_released(self, hosts, mapping, worker, es, monkeypatch):
        redis, snap = LosingRedis({"flopo": flopo_entry()}), {}
        monkeypatch.setattr(transfer.RemoteTransfer, "release",
                            self.lose_after(redis, es, hosts, transfer.RemoteTransfer.release, snap))
        with pytest.raises(update_jobs.OwnershipLost):
            await run(hosts, redis, es)
        self.assert_nothing_after(redis, es, hosts, snap)
        assert (hosts.central / "flopo" / "flopo.owl").read_bytes() == OLD, "central's copy was refreshed"
        assert es.aliases["flopo"] == V1, "the alias moved"
        assert on_host(hosts, "flopo/flopo.owl") == NEW  # promoted before the loss

    async def test_after_the_stage_is_aborted(self, hosts, mapping, worker, es, monkeypatch):
        worker.validation = {"ok": False, "answered": True, "message": "not an ontology"}
        redis, snap = LosingRedis({"flopo": flopo_entry()}), {}
        monkeypatch.setattr(transfer.RemoteTransfer, "abort",
                            self.lose_after(redis, es, hosts, transfer.RemoteTransfer.abort, snap))
        with pytest.raises(update_jobs.OwnershipLost):
            await run(hosts, redis, es)
        self.assert_nothing_after(redis, es, hosts, snap)
        assert snap["entry"]["update_txn"]["phase"] == "validate"

    async def test_after_the_export_is_discarded(self, hosts, mapping, worker, es, monkeypatch):
        es.create_ok = False
        redis, snap = LosingRedis({"flopo": flopo_entry()}), {}
        monkeypatch.setattr(updater, "discard_export", self.lose_after(redis, es, hosts, worker.discard, snap))
        with pytest.raises(update_jobs.OwnershipLost):
            await run(hosts, redis, es)
        self.assert_nothing_after(redis, es, hosts, snap)
        assert ("delete", NEW_INDEX) not in es.calls


# ---------------------------------------------------------------------------
# HTTPS with a private CA
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is needed to make the test certificates")
    return make_tls_files(tmp_path_factory.mktemp("tls"), openssl)


@pytest.fixture
def tls_service(hosts, tls_files):
    app = aberowl_transfer.create_app(service_config(hosts.worker, hosts.state))
    with ServiceThread(app, ssl_certfile=str(tls_files / "server.pem"),
                       ssl_keyfile=str(tls_files / "server.key")) as svc:
        yield svc


class TestTLS:

    async def test_an_update_over_https_with_the_configured_ca(self, hosts, tls_service, tls_files, worker, es,
                                                              monkeypatch):
        assert tls_service.url.startswith("https://")
        write_mapping(hosts, monkeypatch, [{"worker_url": WORKER, "transfer_url": tls_service.url,
                                            "token_file": token_file(hosts), "ca_file": str(tls_files / "ca.pem")}])
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "updated", result
        assert on_host(hosts, "flopo/flopo.owl") == NEW

    @pytest.mark.parametrize("ca", ["other-ca.pem", None])
    async def test_an_untrusted_certificate_is_refused(self, hosts, tls_service, tls_files, worker, es,
                                                       monkeypatch, ca):
        entry = {"worker_url": WORKER, "transfer_url": tls_service.url, "token_file": token_file(hosts)}
        if ca:
            entry["ca_file"] = str(tls_files / ca)
        write_mapping(hosts, monkeypatch, [entry])
        before = snapshot(hosts.worker)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(hosts, redis, es)
        assert result["status"] == "skipped" and result["error"] == "transfer_unreachable"
        assert "certificate" in result["message"].lower()
        assert "download" not in worker.kinds() and snapshot(hosts.worker) == before
