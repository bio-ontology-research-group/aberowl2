"""
Ontology update pipeline (central_server/app/intake/updater.py) and its admin
endpoints.

Production FLOPO stayed stale although newer releases were published. The
first repair made updates run for selected ontologies; review then found
defects in how a run ends when something fails or its outcome is unknown.
The tests below reproduce each of them:

- the status poll wrote back a stale copy of the registry entry (B1)
- a partial update stored the new version markers, so it became "unchanged" (B2)
- indexing read the raw file with network imports and could promote an
  incomplete index (B3)
- an ES error read as "no index" could delete the live index (B4)
- a timed-out task released the guard, so a retry ran over it (B6)
- the worker kept reporting a deleted staging path (B7)
- batch runs overlapped heavy phases on one worker (B8)
- a reindex whose alias move failed reported success
- the previous index was deleted, so search could not be rolled back

Everything is mocked: no network, Docker, Redis or Elasticsearch. Worker
calls are replaced at the updater's function boundary with fakes that follow
the servlets' contracts; the HTTP helpers are tested against fake sessions.
"""

import asyncio
import hashlib
import itertools
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app import es_manager as es_module  # noqa: E402
from app.es_manager import ESUnavailable  # noqa: E402
from app.intake import updater  # noqa: E402

OLD = b"<rdf:RDF><!-- flopo release 1 --></rdf:RDF>\n"
NEW = b"<rdf:RDF><!-- flopo release 2 --></rdf:RDF>\n"
SOURCE = "https://purl.obolibrary.org/obo/flopo.owl"
WORKER = "http://worker-1:8080"
V1 = "aberowl_flopo_classes_v1"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class FakeRedis:
    def __init__(self, entries=None):
        self._data = {"registered_servers": {k: json.dumps(v) for k, v in (entries or {}).items()}}

    async def hget(self, h, k):
        return self._data.get(h, {}).get(k)

    async def hset(self, h, k, v):
        self._data.setdefault(h, {})[k] = v

    async def hkeys(self, h):
        return list(self._data.get(h, {}).keys())

    async def hvals(self, h):
        return list(self._data.get(h, {}).values())

    def entry(self, k):
        raw = self._data["registered_servers"].get(k)
        return json.loads(raw) if raw else None

    def poke(self, k, **fields):
        """A concurrent writer, such as the status poller."""
        e = self.entry(k)
        e.update(fields)
        self._data["registered_servers"][k] = json.dumps(e)


class FakeES:
    """Aliases per ontology and indices with their document counts."""

    def __init__(self, aliases=None):
        self.aliases = dict(aliases if aliases is not None else {"flopo": V1, "po": "aberowl_po_classes_v1"})
        self.indices = {v: 100 for v in self.aliases.values() if v}
        self.calls = []
        self.create_ok = True
        self.swap_ok = True
        self.alias_error = None

    async def get_alias_target(self, oid):
        if self.alias_error:
            raise ESUnavailable(self.alias_error)
        return self.aliases.get(oid)

    async def create_class_index(self, name):
        self.calls.append(("create", name))
        if self.create_ok:
            self.indices[name] = 0
        return self.create_ok

    async def delete_index(self, name):
        assert name not in self.aliases.values(), f"deleted the alias target {name}"
        self.calls.append(("delete", name))
        self.indices.pop(name, None)
        return True

    async def swap_alias(self, oid, new, old):
        self.calls.append(("swap", new, old))
        if self.swap_ok:
            self.aliases[oid] = new
        return self.swap_ok

    async def count_documents(self, name):
        return self.indices.get(name, 0)


def ok_task(message="", **data):
    return {"status": "success", "message": message, "data": {"status": "success", **data}}


class FakeWorker:
    """The source server and the worker servlets, by contract."""

    def __init__(self, es):
        self.es = es
        self.events = []
        self.body = NEW
        self.version = {"changed": True, "etag": '"v2"', "last_modified": None, "need_md5": False,
                        "permanent_redirect": None, "error": None, "http_status": 200}
        self.download_error = None
        self.served = {}            # ontology id -> path reported by listLoadedOntologies
        self.served_state = "ok"
        self.validation = {"ok": True, "answered": True, "message": "", "classCount": 3,
                           "importsPolicy": "suppressed", "declaredImports": []}
        self.export = True
        self.index_start = "started"
        self.indexing = ok_task()
        self.docs = None            # documents an indexing run writes; default classCount
        self.swap_starts = ["started"]
        self.swaps = [ok_task("loaded", serving=True)]
        self.finalize_ok = True
        self.task_state = "success"
        self.reasoners = []
        self.in_flight = 0
        self.peak = 0
        self._n = itertools.count(1)
        self._index_of = {}

    def kinds(self):
        return [e[0] for e in self.events]

    async def worker_served_path(self, server_url, oid):
        self.events.append(("served", oid))
        if self.served_state != "ok":
            return {"state": self.served_state, "message": "down"}
        return {"state": "ok", "path": self.served.get(oid, f"/data/{oid}/{oid}.owl"), "status": "classified"}

    async def check_version(self, url, etag, lm, md5_, session, headers=None):
        self.events.append(("head", url))
        return dict(self.version)

    async def download(self, url, dest, session, headers=None):
        self.events.append(("download", url))
        if self.download_error:
            return {"error": self.download_error}
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.body)
        return {"md5": md5(self.body), "size": len(self.body)}

    async def validate(self, server_url, path, oid=None, secret_key="", served_path=None, export_id=None):
        self.events.append(("validate", path, oid, served_path, export_id))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        result = dict(self.validation)
        if result["ok"] and self.export and export_id:
            result["exportPath"] = f"/tmp/exports/{export_id}/{oid}.owl"
        return result

    async def discard(self, server_url, secret_key, oid, export_id):
        self.events.append(("discard", export_id))
        return True

    async def start_indexing(self, server_url, secret_key, oid, path, index, name, description,
                             ontology_index="aberowl_ontologies"):
        self.events.append(("index", path, index))
        if self.index_start != "started":
            return {"state": self.index_start, "message": f"index {self.index_start}"}
        task = f"idx-{next(self._n)}"
        self._index_of[task] = index
        return {"state": "started", "task_id": task}

    async def start_hotswap(self, server_url, secret_key, oid, path, served_path=None, reasoner_type=None):
        self.events.append(("hotswap", path, served_path))
        self.reasoners.append(reasoner_type)
        state = self.swap_starts.pop(0) if len(self.swap_starts) > 1 else self.swap_starts[0]
        if state != "started":
            return {"state": state, "message": f"hot-swap {state}"}
        return {"state": "started", "task_id": f"swap-{next(self._n)}"}

    async def poll_task(self, server_url, task_id, timeout_secs=None):
        self.events.append(("poll", task_id))
        if task_id.startswith("idx"):
            if self.indexing["status"] == "success":
                index = self._index_of[task_id]
                docs = self.docs if self.docs is not None else self.validation.get("classCount", 50)
                self.es.indices[index] = docs
            return dict(self.indexing)
        return dict(self.swaps.pop(0) if len(self.swaps) > 1 else self.swaps[0])

    async def finalize(self, server_url, secret_key, oid, staged, served):
        self.events.append(("finalize", staged, served))
        if self.finalize_ok:
            return {"ok": True, "message": "", "path": served}
        return {"ok": False, "message": "HTTP 409", "path": staged}

    async def task_status_once(self, server_url, task_id):
        return self.task_state


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(updater, "_locks", {})
    monkeypatch.setattr(updater, "_worker_locks", {})
    ids = (f"txn-{i}" for i in itertools.count(1))
    monkeypatch.setattr(updater, "new_txn_id", lambda: next(ids))
    # The shared-directory default; tests/test_update_transfer.py covers the rest.
    monkeypatch.delenv("UPDATE_TRANSFER_CONFIG", raising=False)


@pytest.fixture
def corpus(tmp_path):
    for oid in ("flopo", "po"):
        (tmp_path / oid).mkdir()
        (tmp_path / oid / f"{oid}.owl").write_bytes(OLD)
    return tmp_path


def flopo_entry(**over):
    e = {"ontology": "flopo", "ontology_id": "flopo", "name": "FLOPO", "url": WORKER,
         "status": "online", "class_count": 100, "source": "manual", "source_url": SOURCE,
         "source_etag": '"v1"', "source_md5": md5(OLD), "update_status": "ok"}
    e.update(over)
    return e


def po_entry(**over):
    values = dict(ontology="po", ontology_id="po", source_url="https://purl.obolibrary.org/obo/po.owl")
    values.update(over)
    return flopo_entry(**values)


@pytest.fixture
def es():
    return FakeES()


@pytest.fixture
def worker(monkeypatch, es):
    w = FakeWorker(es)
    monkeypatch.setattr(updater, "worker_served_path", w.worker_served_path)
    monkeypatch.setattr(updater, "check_version", w.check_version)
    monkeypatch.setattr(updater, "download_ontology", w.download)
    monkeypatch.setattr(updater, "validate_staged", w.validate)
    monkeypatch.setattr(updater, "discard_export", w.discard)
    monkeypatch.setattr(updater, "start_indexing", w.start_indexing)
    monkeypatch.setattr(updater, "start_hotswap", w.start_hotswap)
    monkeypatch.setattr(updater, "poll_task", w.poll_task)
    monkeypatch.setattr(updater, "finalize_served_path", w.finalize)
    monkeypatch.setattr(updater, "task_status_once", w.task_status_once)
    return w


async def run(corpus, redis, es, oid="flopo", **kw):
    return await updater.execute_update_pipeline(
        ontology_id=oid, registry_entry=redis.entry(oid), redis_client=redis, es_mgr=es,
        ontologies_base_path=str(corpus), es_url="http://es:9200", **kw)


async def reindex(corpus, redis, es, oid="flopo"):
    return await updater.execute_reindex(oid, redis.entry(oid), redis, es, str(corpus), "http://es:9200")


def served(corpus, oid="flopo"):
    return (corpus / oid / f"{oid}.owl").read_bytes()


def txn_dir(corpus, txn="txn-1", oid="flopo"):
    return corpus / oid / "updates" / txn


STAGING = "/data/flopo/updates/txn-1/flopo.owl"
SERVED = "/data/flopo/flopo.owl"
EXPORT = "/tmp/exports/txn-1/flopo.owl"
NEW_INDEX = "aberowl_flopo_classes_txn-1"


def assert_recovery(redis, error, phase=None, oid="flopo"):
    e = redis.entry(oid)
    txn = e.get("update_txn")
    assert txn and txn["state"] == "recovery_required", e.get("update_txn")
    assert txn["error"].startswith(error), txn["error"]
    if phase:
        assert txn["phase"] == phase
    assert e["update_status"] == "recovery_required"
    return txn


def assert_clean_end(redis, oid="flopo"):
    assert "update_txn" not in redis.entry(oid)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDetection:

    async def test_unchanged_head_is_a_noop(self, corpus, worker, es):
        worker.version.update(changed=False, etag='"v1"')
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["status"] == "unchanged" and result["success"]
        assert worker.kinds() == ["served", "head"]
        assert es.calls == [] and served(corpus) == OLD
        e = redis.entry("flopo")
        assert e["last_check_status"] == "unchanged" and e["update_status"] == "ok"
        assert_clean_end(redis)

    async def test_first_check_matching_the_served_file_records_a_baseline(self, corpus, worker, es):
        worker.body = OLD
        worker.version.update(etag='"up-1"')
        redis = FakeRedis({"flopo": flopo_entry(source_etag=None, source_md5=None)})
        result = await run(corpus, redis, es)
        assert result["status"] == "unchanged"
        assert worker.kinds() == ["served", "head", "download"]
        assert es.calls == [] and not txn_dir(corpus).exists()
        e = redis.entry("flopo")
        assert e["source_etag"] == '"up-1"' and e["source_md5"] == md5(OLD)
        assert_clean_end(redis)

    async def test_an_unfinished_run_is_not_taken_for_a_baseline(self, corpus, worker, es):
        """No MD5 recorded yet, and the last run stopped for recovery, possibly
        after replacing the served file: matching it proves nothing about the
        search index, so the update runs in full."""
        (corpus / "flopo" / "flopo.owl").write_bytes(NEW)
        history = [{"status": "recovery_required", "txn": "txn-0"}, {"status": "resolved_by_operator", "txn": "txn-0"}]
        redis = FakeRedis({"flopo": flopo_entry(source_etag=None, source_md5=None, update_history=history)})
        result = await run(corpus, redis, es)
        assert result["status"] == "updated" and es.aliases["flopo"] == NEW_INDEX

    async def test_force_updates_an_unchanged_source(self, corpus, worker, es):
        worker.version.update(changed=False, etag='"v1"')
        worker.body = OLD
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es, force=True)
        assert result["status"] == "updated" and "hotswap" in worker.kinds()

    async def test_unchanged_but_never_indexed_is_indexed(self, corpus, worker):
        es = FakeES({"flopo": None})
        worker.es = es
        worker.version.update(changed=False, etag='"v1"')
        worker.body = OLD
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["status"] == "updated" and es.aliases["flopo"] == NEW_INDEX


# ---------------------------------------------------------------------------
# Refusals before a transaction begins
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRefusals:

    async def test_missing_source_is_a_recorded_skip(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry(source_url=None)})
        result = await run(corpus, redis, es)
        assert result["status"] == "skipped" and result["error"] == "no_source_url"
        assert worker.events == []
        e = redis.entry("flopo")
        assert e["last_check_status"] == "skipped" and e["update_status"] == "ok"

    async def test_served_file_not_visible_is_a_skip(self, tmp_path, worker, es):
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(tmp_path, redis, es)
        assert result["error"] == "served_file_missing" and worker.events == []

    async def test_no_worker_is_a_skip(self, corpus, worker, es):
        result = await run(corpus, FakeRedis({"flopo": flopo_entry(url=None)}), es)
        assert result["error"] == "no_server_url" and worker.events == []

    async def test_invalid_id_is_refused(self, corpus, worker, es):
        result = await run(corpus, FakeRedis({"../x": flopo_entry()}), es, oid="../x")
        assert result["error"] == "invalid_ontology_id" and worker.events == []

    async def test_worker_serving_another_path_is_refused(self, corpus, worker, es):
        """B7: an update replaces /data/<id>/<id>.owl; a worker that loads
        something else (here a staging path left by an earlier run) would
        lose it on restart, and its state needs reconciling first."""
        worker.served["flopo"] = "/data/flopo/updates/old/flopo.owl"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "worker_path_mismatch" and "updates/old" in result["message"]
        assert worker.kinds() == ["served"]

    @pytest.mark.parametrize("state,error", [("not_loaded", "worker_not_serving"),
                                             ("unreachable", "worker_unreachable")])
    async def test_worker_state_is_checked_first(self, corpus, worker, es, state, error):
        worker.served_state = state
        result = await run(corpus, FakeRedis({"flopo": flopo_entry()}), es)
        assert result["error"] == error and worker.kinds() == ["served"]

    async def test_es_error_is_not_read_as_no_index(self, corpus, worker, es):
        """B4: an ES error used to look like a missing alias; the next index
        name was then v1, the live index, which was deleted and recreated."""
        es.alias_error = "GET /_alias/aberowl_flopo_classes: HTTP 503"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "es_unavailable"
        assert es.calls == [] and "download" not in worker.kinds()
        assert_clean_end(redis)

    async def test_head_http_error_fails_without_download(self, corpus, worker, es):
        worker.version.update(changed=False, error="http_403", http_status=403)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "http_403" and "download" not in worker.kinds()
        assert redis.entry("flopo")["update_status"] == "check_failed"

    async def test_source_gone(self, corpus, worker, es):
        worker.version.update(error="source_gone")
        redis = FakeRedis({"flopo": flopo_entry()})
        assert (await run(corpus, redis, es))["error"] == "source_gone"

    async def test_missing_bioportal_key_fails_before_any_request(self, corpus, worker, es, monkeypatch):
        monkeypatch.setenv("BIOPORTAL_API_KEY", "")
        redis = FakeRedis({"flopo": flopo_entry(source_url="https://data.bioontology.org/ontologies/FLOPO/download")})
        result = await run(corpus, redis, es)
        assert result["error"] == "bioportal_api_key_missing" and worker.events == []


# ---------------------------------------------------------------------------
# Known failures: cleaned up, transaction ended
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestKnownFailures:

    async def test_download_failure(self, corpus, worker, es):
        worker.download_error = "HTTP 502"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["status"] == "failed" and "HTTP 502" in result["error"]
        assert es.calls == [] and served(corpus) == OLD and not txn_dir(corpus).exists()
        assert redis.entry("flopo")["update_status"] == "download_failed"
        assert_clean_end(redis)

    async def test_validation_failure_keeps_everything(self, corpus, worker, es):
        msg = "Imports of flopo: <http://x/new.owl>, imported by <flopo>, is not listed in the catalogue"
        worker.validation = {"ok": False, "answered": True, "message": msg}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "owl_validation_failed" and msg in result["message"]
        assert "index" not in worker.kinds() and "hotswap" not in worker.kinds()
        assert es.calls == [] and served(corpus) == OLD and not txn_dir(corpus).exists()
        e = redis.entry("flopo")
        assert e["update_status"] == "validation_failed" and e["source_etag"] == '"v1"'
        assert_clean_end(redis)

    async def test_unanswered_validation_keeps_input_and_requires_recovery(self, corpus, worker, es):
        worker.validation = {"ok": False, "answered": False, "message": "no answer"}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, "validation_unknown", phase="validate")
        assert txn_dir(corpus).exists()
        assert "discard" not in worker.kinds() and "index" not in worker.kinds()
        assert served(corpus) == OLD
        assert (await run(corpus, redis, es))["error"] == "recovery_required"

    async def test_validation_is_sent_the_served_path_and_transaction(self, corpus, worker, es):
        """B5 and B3: the worker resolves catalogue entries naming the served
        file to the staged root, and exports the merged ontology."""
        await run(corpus, FakeRedis({"flopo": flopo_entry()}), es)
        assert ("validate", STAGING, "flopo", SERVED, "txn-1") in worker.events

    async def test_an_outdated_worker_is_not_indexed(self, corpus, worker, es):
        worker.export = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "worker_outdated" and "index" not in worker.kinds()
        assert_clean_end(redis)

    async def test_index_create_failure(self, corpus, worker, es):
        es.create_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "es_index_create_failed" and "index" not in worker.kinds()
        assert ("discard", "txn-1") in worker.events
        assert_clean_end(redis)

    async def test_refused_indexing(self, corpus, worker, es):
        worker.index_start = "refused"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "indexing_trigger_failed"
        assert ("delete", NEW_INDEX) in es.calls and ("discard", "txn-1") in worker.events
        assert not txn_dir(corpus).exists()
        assert_clean_end(redis)

    async def test_failed_indexing_deletes_its_index(self, corpus, worker, es):
        worker.indexing = {"status": "failed", "message": "Indexer exited 1", "data": {}}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "es_indexing_failed" and "Indexer exited 1" in result["message"]
        assert ("delete", NEW_INDEX) in es.calls and es.aliases["flopo"] == V1
        assert "hotswap" not in worker.kinds() and served(corpus) == OLD
        assert_clean_end(redis)

    async def test_indexing_reads_the_export(self, corpus, worker, es):
        """B3: the staged root has owl:imports, which the standalone indexer
        would fetch over the network; the export has none."""
        await run(corpus, FakeRedis({"flopo": flopo_entry()}), es)
        assert ("index", EXPORT, NEW_INDEX) in worker.events
        assert not any(e[0] == "index" and e[1] == STAGING for e in worker.events)

    async def test_incomplete_index_is_never_promoted(self, corpus, worker, es):
        """B3: the indexer swallowed document errors and exited 0; the count
        now has to match the validated classes."""
        worker.docs = 2
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "index_incomplete" and "2 documents for 3" in result["message"]
        assert ("delete", NEW_INDEX) in es.calls and "hotswap" not in worker.kinds()
        assert es.aliases["flopo"] == V1
        assert_clean_end(redis)

    async def test_refused_hotswap(self, corpus, worker, es):
        worker.swap_starts = ["refused"]
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "hotswap_trigger_failed" and served(corpus) == OLD
        assert ("delete", NEW_INDEX) in es.calls
        assert_clean_end(redis)

    async def test_failed_swap_with_the_previous_copy_serving(self, corpus, worker, es):
        """S1: with an import catalogue the old copy keeps serving after a
        read failure; reloading it would only cause an outage."""
        worker.swaps = [{"status": "failed", "message": "Imports of flopo: not listed",
                         "data": {"status": "failed", "serving": True, "servedFrom": SERVED}}]
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "hotswap_failed" and "previous copy is serving" in result["message"]
        assert [e for e in worker.events if e[0] == "hotswap"] == [("hotswap", STAGING, SERVED)]
        assert ("delete", NEW_INDEX) in es.calls and served(corpus) == OLD
        assert_clean_end(redis)

    @pytest.mark.parametrize("data", [{"serving": False}, {}])
    async def test_failed_swap_reloads_the_served_file(self, corpus, worker, es, data):
        worker.swaps = [{"status": "failed", "message": "OutOfMemoryError", "data": data}, ok_task()]
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["error"] == "hotswap_failed" and "served file was reloaded" in result["message"]
        swaps = [e for e in worker.events if e[0] == "hotswap"]
        assert swaps == [("hotswap", STAGING, SERVED), ("hotswap", SERVED, SERVED)]
        assert ("delete", NEW_INDEX) in es.calls and es.aliases["flopo"] == V1
        assert served(corpus) == OLD and not txn_dir(corpus).exists()
        assert_clean_end(redis)

    @pytest.mark.parametrize("configured,sent", [("hermit", "hermit"), ("unknown", None), (None, None)])
    async def test_hotswap_keeps_the_configured_reasoner(self, corpus, worker, es, configured, sent):
        worker.swaps = [{"status": "failed", "message": "x", "data": {"serving": False}}, ok_task()]
        await run(corpus, FakeRedis({"flopo": flopo_entry(reasoner_configured=configured)}), es)
        assert worker.reasoners == [sent, sent], "the swap and the reload of the served file"


# ---------------------------------------------------------------------------
# Unknown outcomes: recovery required, resources kept, retries refused
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUnknownOutcomes:

    async def test_hotswap_timeout_keeps_everything_and_blocks_retries(self, corpus, worker, es, monkeypatch):
        """B6: a timeout released the lock while the worker kept swapping; a
        retry reused the staging file and index and ran a second reload."""
        worker.swaps = [{"status": "timeout", "message": "no result after 1800s", "data": {}}]
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["recovery_required"] and result["error"] == "hotswap_unknown"
        txn = assert_recovery(redis, "hotswap_unknown", phase="hotswap")
        assert txn["task_id"].startswith("swap-") and txn["worker"] == WORKER
        assert txn["index"] == NEW_INDEX and txn["staging"] == STAGING
        assert txn_dir(corpus).exists() and NEW_INDEX in es.indices
        assert [e for e in worker.events if e[0] == "hotswap"] == [("hotswap", STAGING, SERVED)]
        assert served(corpus) == OLD and redis.entry("flopo")["source_etag"] == '"v1"'

        # A new central process has no in-memory lock; the record still blocks.
        monkeypatch.setattr(updater, "_locks", {})
        worker.events.clear()
        again = await run(corpus, redis, es)
        assert again["status"] == "skipped" and again["error"] == "recovery_required"
        assert worker.events == []
        assert (await reindex(corpus, redis, es))["error"] == "recovery_required"

    async def test_lost_task_is_not_taken_as_a_restart(self, corpus, worker, es):
        """A 404 for a task does not prove the worker restarted; nothing it
        may use is deleted."""
        worker.swaps = [{"status": "lost", "message": "task swap-2 unknown to the worker", "data": {}}]
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        assert_recovery(redis, "hotswap_unknown")
        assert txn_dir(corpus).exists() and NEW_INDEX in es.indices
        assert not any(c[0] == "delete" for c in es.calls)

    @pytest.mark.parametrize("phase", ["index", "hotswap"])
    async def test_an_unanswered_start_may_have_started(self, corpus, worker, es, phase):
        """The request may have reached the worker even though the answer
        was lost."""
        if phase == "index":
            worker.index_start = "unknown"
        else:
            worker.swap_starts = ["unknown"]
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, f"{'indexing' if phase == 'index' else 'hotswap'}_unknown", phase=phase)
        assert NEW_INDEX in es.indices and txn_dir(corpus).exists()

    async def test_indexing_timeout(self, corpus, worker, es):
        worker.indexing = {"status": "timeout", "message": "no result", "data": {}}
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        assert_recovery(redis, "indexing_unknown", phase="index")
        assert NEW_INDEX in es.indices and "hotswap" not in worker.kinds()

    async def test_failed_restore_needs_recovery(self, corpus, worker, es):
        worker.swaps = [{"status": "failed", "message": "x", "data": {"serving": False}},
                        {"status": "failed", "message": "y", "data": {}}]
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        assert_recovery(redis, "restore_failed", phase="restore")
        assert NEW_INDEX in es.indices

    async def test_unexpected_exception(self, corpus, worker, es, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("bug")
        monkeypatch.setattr(updater, "validate_staged", boom)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["recovery_required"] and result["error"] == "unexpected"
        assert_recovery(redis, "unexpected", phase="validate")
        assert txn_dir(corpus).exists()

    async def test_cancellation(self, corpus, worker, es, monkeypatch):
        gate = asyncio.Event()

        async def hang(*a, **k):
            worker.events.append(("validate",))
            await gate.wait()
        monkeypatch.setattr(updater, "validate_staged", hang)
        redis = FakeRedis({"flopo": flopo_entry()})
        task = asyncio.create_task(run(corpus, redis, es))
        while ("validate",) not in worker.events:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_recovery(redis, "cancelled")
        assert txn_dir(corpus).exists()
        assert not updater.update_in_progress("flopo")

    async def test_interrupted_central_leaves_a_running_record(self, corpus, worker, es):
        """A record still "running" after a restart blocks like any other."""
        redis = FakeRedis({"flopo": flopo_entry(update_txn={
            "id": "txn-0", "kind": "update", "phase": "hotswap", "state": "running", "worker": WORKER})})
        result = await run(corpus, redis, es)
        assert result["error"] == "recovery_required" and "txn-0" in result["message"]
        assert worker.events == []


# ---------------------------------------------------------------------------
# After the swap: promotion, finalization, alias
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdate:

    async def test_successful_update_sequence(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["status"] == "updated" and result["success"] and result["changed"] is True

        assert worker.events[:3] == [("served", "flopo"), ("head", SOURCE), ("download", SOURCE)]
        assert worker.events[3:] == [
            ("validate", STAGING, "flopo", SERVED, "txn-1"),
            ("index", EXPORT, NEW_INDEX),
            ("poll", "idx-1"),
            ("hotswap", STAGING, SERVED),
            ("poll", "swap-2"),
            ("finalize", STAGING, SERVED),
        ]
        # The previous index is kept for rollback.
        assert es.calls == [("create", NEW_INDEX), ("swap", NEW_INDEX, V1)]
        assert es.aliases["flopo"] == NEW_INDEX and V1 in es.indices

        assert served(corpus) == NEW and not txn_dir(corpus).exists()
        archived = list((corpus / "flopo" / "archive").iterdir())
        assert len(archived) == 1 and archived[0].read_bytes() == OLD

        e = redis.entry("flopo")
        assert e["source_etag"] == '"v2"' and e["source_md5"] == md5(NEW)
        assert e["update_status"] == "ok" and e["last_check_status"] == "updated"
        assert e["active_es_index"] == NEW_INDEX and e["previous_es_index"] == V1
        assert e["active_owl_path"] == SERVED and e["archived_owl_path"] == str(archived[0])
        assert e["update_history"][-1]["status"] == "success" and e["update_history"][-1]["txn"] == "txn-1"
        assert e["class_count"] == 100 and e["url"] == WORKER
        assert_clean_end(redis)

    async def test_the_index_before_the_previous_one_is_retired(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        worker.body = b"<rdf:RDF><!-- release 3 --></rdf:RDF>\n"
        worker.version.update(etag='"v3"')
        await run(corpus, redis, es)
        assert es.aliases["flopo"] == "aberowl_flopo_classes_txn-2"
        assert NEW_INDEX in es.indices, "the previous index is kept"
        assert ("delete", V1) in es.calls and V1 not in es.indices
        assert redis.entry("flopo")["previous_es_index"] == NEW_INDEX

    async def test_the_alias_target_is_never_retired(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry(previous_es_index=V1)})
        await run(corpus, redis, es)
        assert V1 in es.indices, "V1 was the alias target when the run began"

    async def test_failed_finalize_needs_recovery(self, corpus, worker, es):
        """B7: the worker keeps recording the staging path; the staging file
        stays so that path stays valid."""
        worker.finalize_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, "finalize_failed", phase="finalize")
        assert served(corpus) == NEW and (txn_dir(corpus) / "flopo.owl").read_bytes() == NEW
        assert es.aliases["flopo"] == V1 and redis.entry("flopo")["source_etag"] == '"v1"'

    async def test_failed_promotion_needs_recovery(self, corpus, worker, es, monkeypatch):
        def no_space(paths):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(updater, "_promote", no_space)
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        assert_recovery(redis, "promote_failed", phase="promote")
        assert served(corpus) == OLD and txn_dir(corpus).exists()
        assert "finalize" not in worker.kinds()

    async def test_failed_alias_move_is_not_success_and_is_not_forgotten(self, corpus, worker, es):
        """B2: the new version markers were stored, so the next run found the
        source unchanged and set the status back to ok, with search stale."""
        es.swap_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await run(corpus, redis, es)
        assert result["status"] == "failed" and result["recovery_required"]
        assert_recovery(redis, "alias_swap_failed", phase="alias")
        e = redis.entry("flopo")
        assert e["source_etag"] == '"v1"' and e["source_md5"] == md5(OLD), "markers unchanged"
        assert NEW_INDEX in es.indices and es.aliases["flopo"] == V1
        assert served(corpus) == NEW, "the worker serves the new release, and so does the served file"

        worker.events.clear()
        worker.version.update(changed=False)
        again = await run(corpus, redis, es)
        assert again["error"] == "recovery_required" and worker.events == []
        assert redis.entry("flopo")["update_status"] == "recovery_required"

    async def test_unreadable_alias_after_the_swap(self, corpus, worker, es, monkeypatch):
        real = es.swap_alias

        async def swap_then_fail(oid, new, old):
            await real(oid, new, old)
            es.alias_error = "timeout"
            return True
        monkeypatch.setattr(es, "swap_alias", swap_then_fail)
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        assert_recovery(redis, "es_unavailable", phase="alias")

    async def test_registry_writes_made_during_the_update_survive(self, corpus, worker, es, monkeypatch):
        redis = FakeRedis({"flopo": flopo_entry()})
        real = worker.validate

        async def validate_and_poll(*a, **k):
            redis.poke("flopo", class_count=250)
            return await real(*a, **k)
        monkeypatch.setattr(updater, "validate_staged", validate_and_poll)
        await run(corpus, redis, es)
        assert redis.entry("flopo")["class_count"] == 250

    async def test_archive_keeps_the_newest(self, corpus, worker, es, monkeypatch):
        monkeypatch.setattr(updater, "ARCHIVE_KEEP", 2)
        archive = corpus / "flopo" / "archive"
        archive.mkdir()
        for stamp in ("20250101T000000", "20250201T000000", "20250301T000000"):
            (archive / f"flopo_{stamp}.owl").write_bytes(b"old")
        (archive / "notes.txt").write_text("operator file")
        await run(corpus, FakeRedis({"flopo": flopo_entry()}), es)
        names = sorted(p.name for p in archive.iterdir())
        assert "notes.txt" in names
        kept = [n for n in names if n.endswith(".owl")]
        assert len(kept) == 2 and "flopo_20250301T000000.owl" in kept

    async def test_disabled_status_survives_a_manual_run(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry(update_status="disabled")})
        await run(corpus, redis, es)
        e = redis.entry("flopo")
        assert e["update_status"] == "disabled" and e["last_check_status"] == "updated"

    async def test_concurrent_run_is_skipped(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry()})
        async with updater._lock_for("flopo"):
            assert updater.update_in_progress("flopo")
            result = await run(corpus, redis, es)
            again = await reindex(corpus, redis, es)
        assert result["error"] == "update_in_progress" and again["error"] == "update_in_progress"
        assert worker.events == []

    async def test_one_heavy_phase_per_worker(self, corpus, worker, es):
        """B8: batch checks ran up to five pipelines at once; on one worker
        that meant parallel full parses, indexers and swaps in one heap."""
        redis = FakeRedis({"flopo": flopo_entry(), "po": po_entry()})
        await asyncio.gather(run(corpus, redis, es, oid="flopo"), run(corpus, redis, es, oid="po"))
        assert worker.peak == 1
        assert redis.entry("flopo")["update_status"] == "ok" and redis.entry("po")["update_status"] == "ok"

    async def test_different_workers_proceed_together(self, corpus, worker, es):
        redis = FakeRedis({"flopo": flopo_entry(), "po": po_entry(url="http://worker-2:8080")})
        await asyncio.gather(run(corpus, redis, es, oid="flopo"), run(corpus, redis, es, oid="po"))
        assert worker.peak == 2


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReindex:

    async def test_reindex_keeps_the_previous_index(self, corpus, worker, es):
        worker.docs = 40
        worker.validation["classCount"] = 40
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["success"] and result["documents"] == 40
        assert ("index", EXPORT, NEW_INDEX) in worker.events
        assert es.aliases["flopo"] == NEW_INDEX and V1 in es.indices
        e = redis.entry("flopo")
        assert e["active_es_index"] == NEW_INDEX and e["previous_es_index"] == V1
        assert e["update_status"] == "ok"
        assert_clean_end(redis)

    async def test_reindex_uses_the_pinned_import_export(self, corpus, worker, es):
        worker.validation.update(importsPolicy="catalogue", importsDeclared=1,
                                 importsResolved=1, importsUnresolved=0)
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["success"]
        assert ("validate", SERVED, "flopo", SERVED, "txn-1") in worker.events
        assert ("index", EXPORT, NEW_INDEX) in worker.events
        assert "download" not in worker.kinds() and "hotswap" not in worker.kinds()

    async def test_reindex_invalid_imports_leave_live_index_untouched(self, corpus, worker, es):
        worker.validation = {"ok": False, "answered": True, "message": "missing pinned import"}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "reindex_validation_failed"
        assert es.aliases["flopo"] == V1 and NEW_INDEX not in es.indices
        assert "index" not in worker.kinds()
        assert_clean_end(redis)

    async def test_reindex_unknown_validation_keeps_recovery_guard(self, corpus, worker, es):
        worker.validation = {"ok": False, "answered": False, "message": "no response"}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, "validation_unknown")
        assert NEW_INDEX not in es.indices and "index" not in worker.kinds()

    async def test_reindex_refused_task_discards_owned_export(self, corpus, worker, es):
        worker.index_start = "refused"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "reindex_trigger_failed"
        assert ("discard", "txn-1") in worker.events
        assert es.aliases["flopo"] == V1 and NEW_INDEX not in es.indices

    async def test_reindex_incomplete_index_is_not_promoted(self, corpus, worker, es):
        worker.docs = worker.validation["classCount"] - 1
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "index_count_mismatch"
        assert es.aliases["flopo"] == V1 and NEW_INDEX not in es.indices
        assert_clean_end(redis)

    async def test_reindex_without_a_worker_export_does_not_index(self, corpus, worker, es):
        worker.export = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "worker_outdated"
        assert ("discard", "txn-1") in worker.events
        assert "index" not in worker.kinds() and es.aliases["flopo"] == V1
        assert served(corpus) == OLD
        assert_clean_end(redis)

    async def test_reindex_create_failure_discards_unused_export(self, corpus, worker, es):
        es.create_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "es_index_create_failed"
        assert ("discard", "txn-1") in worker.events
        assert "index" not in worker.kinds() and es.aliases["flopo"] == V1
        assert_clean_end(redis)

    async def test_reindex_unknown_start_retains_its_export_and_guard(self, corpus, worker, es):
        worker.index_start = "unknown"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["recovery_required"]
        txn = assert_recovery(redis, "indexing_unknown")
        assert txn["export"] == EXPORT and NEW_INDEX in es.indices
        assert "discard" not in worker.kinds() and es.aliases["flopo"] == V1

    async def test_reindex_with_a_failed_alias_move_is_a_failure(self, corpus, worker, es):
        """It used to record the new index as active, with status ok."""
        es.swap_ok = False
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert not result["success"] and result["error"] == "alias_swap_failed"
        assert es.aliases["flopo"] == V1 and NEW_INDEX not in es.indices
        e = redis.entry("flopo")
        assert e["update_status"] == "reindex_failed" and e.get("active_es_index") != NEW_INDEX
        assert_clean_end(redis)

    async def test_empty_reindex_is_not_promoted(self, corpus, worker, es):
        worker.docs = 0
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "index_empty" and es.aliases["flopo"] == V1
        assert NEW_INDEX not in es.indices

    async def test_reindex_indexing_timeout_needs_recovery(self, corpus, worker, es):
        worker.indexing = {"status": "timeout", "message": "x", "data": {}}
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["recovery_required"]
        assert_recovery(redis, "indexing_unknown")
        assert NEW_INDEX in es.indices

    async def test_reindex_es_error_changes_nothing(self, corpus, worker, es):
        es.alias_error = "HTTP 500"
        redis = FakeRedis({"flopo": flopo_entry()})
        result = await reindex(corpus, redis, es)
        assert result["error"] == "es_unavailable" and es.calls == [] and worker.events == []
        assert_clean_end(redis)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestImports:

    async def test_changed_declared_imports_are_reported(self, corpus, worker, es):
        before = ["http://purl.obolibrary.org/obo/flopo/imports/po_import.owl"]
        after = before + ["http://purl.obolibrary.org/obo/flopo/imports/pato_import.owl"]
        worker.validation.update(importsPolicy="catalogue", declaredImports=after)
        redis = FakeRedis({"flopo": flopo_entry(source_declared_imports=before)})
        await run(corpus, redis, es)
        e = redis.entry("flopo")
        assert e["imports_changed"]["added"] == after[1:] and e["imports_changed"]["removed"] == []
        assert e["source_declared_imports"] == after and e["update_imports_policy"] == "catalogue"

    async def test_first_update_records_a_baseline_only(self, corpus, worker, es):
        worker.validation.update(declaredImports=["http://x/a.owl"])
        redis = FakeRedis({"flopo": flopo_entry()})
        await run(corpus, redis, es)
        e = redis.entry("flopo")
        assert e["source_declared_imports"] == ["http://x/a.owl"] and "imports_changed" not in e

    async def test_dependents_of_an_updated_import_are_marked(self, corpus, worker, es):
        cats = corpus / "import_catalogues"
        cats.mkdir()
        (cats / "flopo.json").write_text(json.dumps(
            {"imports": {"http://purl.obolibrary.org/obo/po.owl": "/data/po/po.owl"}}))
        (cats / "broken.json").write_text("{")
        redis = FakeRedis({"po": po_entry(), "flopo": flopo_entry()})
        result = await run(corpus, redis, es, oid="po")
        assert result["status"] == "updated" and result["dependents"] == ["flopo"]
        assert "po" in redis.entry("flopo")["stale_imports"]
        await run(corpus, redis, es)
        assert "stale_imports" not in redis.entry("flopo")

    def test_catalogue_scan_ignores_other_paths(self, tmp_path):
        cats = tmp_path / "import_catalogues"
        cats.mkdir()
        (cats / "a.json").write_text(json.dumps({"imports": {"i": "/data/po/./po.owl"}}))
        (cats / "b.json").write_text(json.dumps({"imports": {"i": "/data/po/updates/t/po.owl"}}))
        (cats / "c.json").write_text(json.dumps(["not", "an", "object"]))
        assert updater.find_catalogue_dependents(str(tmp_path), "/data/po/po.owl") == ["a"]


# ---------------------------------------------------------------------------
# HTTP boundary: worker servlets and Elasticsearch, by their contracts
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload=None, headers=None, history=(), url=SOURCE, exc=None):
        self.status = status
        self.headers = headers or {}
        self.history = list(history)
        self.url = url
        self._payload = payload
        self._exc = exc

    async def __aenter__(self):
        if self._exc:
            raise self._exc
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    """Answers each request with the next response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, method, url, kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def head(self, url, **kw):
        return self._next("HEAD", url, kw)

    def get(self, url, **kw):
        return self._next("GET", url, kw)

    def post(self, url, **kw):
        return self._next("POST", url, kw)

    def request(self, method, url, **kw):
        return self._next(method, url, kw)


@pytest.fixture
def session(monkeypatch):
    def install(*responses):
        s = _Session(*responses)
        monkeypatch.setattr(updater.aiohttp, "ClientSession", lambda *a, **k: s)
        return s
    return install


@pytest.mark.unit
class TestWorkerContracts:

    @pytest.mark.parametrize("status,error,need_md5", [
        (404, "source_gone", False), (410, "source_gone", False), (401, "http_401", False),
        (403, "http_403", False), (429, "http_429", False), (503, "server_error_503", False),
        (405, None, True), (501, None, True),
    ])
    async def test_head_status_is_never_read_as_unchanged(self, status, error, need_md5):
        r = await updater.check_version(SOURCE, '"v1"', None, None, _Session(_Resp(status)))
        assert r["error"] == error and r["need_md5"] is need_md5 and r["http_status"] == status

    async def test_head_etag(self):
        same = await updater.check_version(SOURCE, '"v1"', None, None, _Session(_Resp(200, headers={"ETag": '"v1"'})))
        new = await updater.check_version(SOURCE, '"v1"', None, None, _Session(_Resp(200, headers={"ETag": '"v2"'})))
        assert same["changed"] is False and new["changed"] is True

    def test_bioportal_key_goes_to_bioportal_only(self, monkeypatch):
        monkeypatch.setenv("BIOPORTAL_API_KEY", "test-key")
        bp = "https://data.bioontology.org/ontologies/FLOPO/download"
        assert updater.source_headers(bp) == {"Authorization": "apikey token=test-key"}
        assert updater.source_headers(SOURCE) == {}
        assert updater.source_headers("https://data.bioontology.org.example.com/x") == {}

    async def test_worker_served_path_reads_list_loaded_ontologies(self, session):
        s = session(_Resp(200, {"status": "ok", "ontologies": [
            {"ontologyId": "go", "path": "/data/go/go.owl", "status": "classified"},
            {"ontologyId": "flopo", "path": STAGING, "status": "classified"}]}))
        r = await updater.worker_served_path(WORKER, "flopo")
        assert r == {"state": "ok", "path": STAGING, "status": "classified"}
        assert s.requests[0][1] == f"{WORKER}/api/listLoadedOntologies.groovy"
        assert (await updater.worker_served_path(WORKER, "po"))["state"] == "not_loaded"

    async def test_worker_served_path_unreachable(self, session):
        session(_Resp(503, {"status": "error"}))
        assert (await updater.worker_served_path(WORKER, "flopo"))["state"] == "unreachable"

    async def test_validate_posts_the_secret_and_transaction(self, session):
        s = session(_Resp(200, {"status": "ok", "classCount": 3, "exportPath": EXPORT}))
        r = await updater.validate_staged(WORKER, STAGING, "flopo", "s3cret", SERVED, "txn-1")
        method, url, kw = s.requests[0]
        assert method == "POST" and url == f"{WORKER}/api/validateOntology.groovy"
        assert kw["json"] == {"secretKey": "s3cret", "owlPath": STAGING, "ontologyId": "flopo",
                              "servedPath": SERVED, "exportId": "txn-1"}
        assert r["ok"] and r["answered"] and r["exportPath"] == EXPORT

    async def test_validate_errors(self, session):
        session(_Resp(400, {"status": "error", "message": "File not found"}))
        assert await updater.validate_staged(WORKER, STAGING) == {
            "ok": False, "answered": True, "message": "File not found"}
        session(_Resp(200, exc=asyncio.TimeoutError()))
        r = await updater.validate_staged(WORKER, STAGING)
        assert r["ok"] is False and r["answered"] is False

    @pytest.mark.parametrize("resp,state", [
        (_Resp(200, {"status": "accepted", "taskId": "t1"}), "started"),
        (_Resp(409, {"status": "error", "message": "task t0 for flopo is still pending"}), "refused"),
        (_Resp(401, {"status": "error", "message": "Unauthorized"}), "refused"),
        (_Resp(500, ValueError("html")), "unknown"),
        (_Resp(200, ValueError("truncated")), "unknown"),
        (_Resp(200, exc=asyncio.TimeoutError()), "unknown"),
    ])
    async def test_start_task_tells_refused_from_unknown(self, session, resp, state):
        session(resp)
        r = await updater.start_hotswap(WORKER, "k", "flopo", STAGING, SERVED, "elk")
        assert r["state"] == state
        if state == "refused" and resp.status == 409:
            assert "still pending" in r["message"]

    async def test_start_hotswap_payload(self, session):
        s = session(_Resp(200, {"taskId": "t1"}))
        await updater.start_hotswap(WORKER, "k", "flopo", STAGING, SERVED, "hermit")
        assert s.requests[0][1] == f"{WORKER}/api/updateOntology.groovy"
        assert s.requests[0][2]["json"] == {"secretKey": "k", "ontologyId": "flopo", "owlPath": STAGING,
                                            "servedPath": SERVED, "reasonerType": "hermit"}

    async def test_start_indexing_payload(self, session):
        s = session(_Resp(200, {"taskId": "idx_1"}))
        await updater.start_indexing(WORKER, "k", "flopo", EXPORT, NEW_INDEX, "FLOPO", "d")
        assert s.requests[0][1] == f"{WORKER}/api/triggerIndexing.groovy"
        assert s.requests[0][2]["json"]["owlPath"] == EXPORT
        assert s.requests[0][2]["json"]["classIndexName"] == NEW_INDEX

    async def test_finalize_payload_and_conflict(self, session):
        s = session(_Resp(409, {"status": "error", "path": STAGING, "message": "recorded as ..."}))
        r = await updater.finalize_served_path(WORKER, "k", "flopo", STAGING, SERVED)
        assert s.requests[0][1] == f"{WORKER}/api/finalizeUpdate.groovy"
        assert s.requests[0][2]["json"] == {"secretKey": "k", "ontologyId": "flopo", "action": "finalize",
                                            "owlPath": STAGING, "servedPath": SERVED}
        assert r["ok"] is False and r["path"] == STAGING

    async def test_discard_export_payload(self, session):
        s = session(_Resp(200, {"status": "ok", "deleted": True}))
        assert await updater.discard_export(WORKER, "k", "flopo", "txn-1")
        assert s.requests[0][2]["json"]["action"] == "discardExport"

    async def test_poll_task_reports_a_lost_task_at_once(self, session, monkeypatch):
        s = session(_Resp(404, {"status": "error"}))
        monkeypatch.setattr(updater, "POLL_INTERVAL", 0.01)
        r = await updater.poll_task(WORKER, "swap-1", timeout_secs=0.05)
        assert r["status"] == "lost" and len(s.requests) == 1

    async def test_poll_task_carries_the_task_record(self, session):
        record = {"status": "failed", "message": "Imports of flopo: not listed", "serving": True}
        session(_Resp(200, record))
        r = await updater.poll_task(WORKER, "swap-1")
        assert r["status"] == "failed" and r["data"]["serving"] is True

    @pytest.mark.parametrize("resp,state", [
        (_Resp(200, {"status": "pending"}), "pending"),
        (_Resp(200, {"status": "success"}), "success"),
        (_Resp(404, {}), "unknown_task"),
        (_Resp(200, exc=OSError("down")), "unreachable"),
    ])
    async def test_task_status_once(self, session, resp, state):
        session(resp)
        assert await updater.task_status_once(WORKER, "t1") == state


@pytest.mark.unit
class TestElasticsearchContracts:

    @pytest.fixture
    def mgr(self):
        return es_module.CentralESManager()

    @pytest.mark.parametrize("resp,expected", [
        (_Resp(200, {V1: {"aliases": {"aberowl_flopo_classes": {}}}}), V1),
        (_Resp(404, {"error": "alias missing", "status": 404}), None),
    ])
    async def test_alias_target(self, session, mgr, resp, expected):
        session(resp)
        assert await mgr.get_alias_target("flopo") == expected

    @pytest.mark.parametrize("resp", [
        _Resp(500, {"error": "x"}), _Resp(503, ValueError("html")), _Resp(200, exc=OSError("refused")),
        _Resp(200, {V1: {}, NEW_INDEX: {}}),
    ])
    async def test_alias_errors_are_not_none(self, session, mgr, resp):
        session(resp)
        with pytest.raises(ESUnavailable):
            await mgr.get_alias_target("flopo")

    async def test_count_refreshes_first(self, session, mgr):
        s = session(_Resp(200, {"_shards": {}}), _Resp(200, {"count": 7}))
        assert await mgr.count_documents(NEW_INDEX) == 7
        assert [r[:2] for r in s.requests] == [("POST", f"{mgr.es_url}/{NEW_INDEX}/_refresh"),
                                               ("GET", f"{mgr.es_url}/{NEW_INDEX}/_count")]

    async def test_count_error(self, session, mgr):
        session(_Resp(200, {}), _Resp(404, {"error": "no index"}))
        with pytest.raises(ESUnavailable):
            await mgr.count_documents(NEW_INDEX)

    def test_transaction_index_does_not_match_the_search_pattern(self):
        import fnmatch
        name = es_module.CentralESManager.transaction_index_name("flopo", "20261015t120000-a1b2c3")
        assert not fnmatch.fnmatch(name, "aberowl_*_classes")
        assert name == name.lower()


# ---------------------------------------------------------------------------
# Status poll (B1)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestStatusPollMerge:

    async def test_poll_does_not_undo_an_update(self, monkeypatch):
        """B1: the sweep read flopo, the update recorded its markers and
        transaction while the worker answered, then the sweep wrote its old
        copy back."""
        import app.main as main_module
        redis = FakeRedis({"flopo": flopo_entry(status="offline")})
        monkeypatch.setattr(main_module, "redis_client", redis)
        snapshot = redis.entry("flopo")

        class Resp:
            status = 200

            async def json(self):
                redis.poke("flopo", source_md5="new-md5", active_es_index=NEW_INDEX,
                           update_txn={"id": "txn-9", "state": "running"})
                return {"status": "classified", "class_count": 120, "title": "Flora Phenotype Ontology"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class S:
            def get(self, *a, **k):
                return Resp()

        await main_module.fetch_and_update_server_metadata(snapshot, session=S())
        e = redis.entry("flopo")
        assert e["source_md5"] == "new-md5" and e["active_es_index"] == NEW_INDEX
        assert e["update_txn"]["id"] == "txn-9"
        assert e["class_count"] == 120 and e["status"] == "online" and e["reasoner_status"] == "classified"


# ---------------------------------------------------------------------------
# Admin endpoints and bounded selection
# ---------------------------------------------------------------------------

AUTH = ("test-admin", "test-pw")
TXN = {"id": "txn-7", "kind": "update", "phase": "hotswap", "state": "recovery_required",
       "error": "hotswap_unknown: timeout", "worker": WORKER, "task_id": "swap-3",
       "index": "aberowl_flopo_classes_txn-7", "staging": "/data/flopo/updates/txn-7/flopo.owl"}


@pytest.fixture
def main_module(monkeypatch, tmp_path):
    import app.main as m
    monkeypatch.setattr(m, "ADMIN_USER", AUTH[0])
    monkeypatch.setattr(m, "ADMIN_PASSWORD", AUTH[1])
    monkeypatch.setattr(m, "es_mgr", MagicMock())
    monkeypatch.setattr(m, "ONTOLOGIES_DIR", str(tmp_path / "in-container"))
    monkeypatch.setattr(m, "ONTOLOGIES_BASE_PATH", "/host/path/not/mounted")
    monkeypatch.setattr(m, "UPDATE_ONTOLOGIES", [])
    return m


@pytest.fixture
def pipeline_calls(monkeypatch):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return {"success": True, "status": "unchanged", "changed": False, "error": None}
    monkeypatch.setattr(updater, "execute_update_pipeline", fake)
    monkeypatch.setattr(updater, "execute_reindex", fake)
    return calls


def client(m):
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test", auth=AUTH)


@pytest.mark.unit
class TestAdmin:

    async def test_set_source(self, main_module):
        redis = FakeRedis({"flopo": flopo_entry(source_url=None, source="obofoundry", source_etag='"x"')})
        main_module.redis_client = redis
        async with client(main_module) as c:
            r = await c.put("/admin/ontology/flopo/source", json={"source_url": SOURCE})
        assert r.status_code == 200
        e = redis.entry("flopo")
        assert e["source_url"] == SOURCE and e["source"] == "manual" and e["source_etag"] is None

    @pytest.mark.parametrize("url", [
        "ftp://example.org/x.owl", "not a url",
        "https://user:pw@example.org/x.owl", "https://data.bioontology.org/x?apikey=abc",
    ])
    async def test_set_source_rejects(self, main_module, url):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry()})
        async with client(main_module) as c:
            r = await c.put("/admin/ontology/flopo/source", json={"source_url": url})
        assert r.status_code == 422

    @pytest.mark.parametrize("method,path,body", [
        ("PUT", "/admin/ontology/flopo/source", {"source_url": SOURCE + "?v=2"}),
        ("POST", "/admin/ontology/flopo/trigger_update", None),
        ("POST", "/admin/ontology/flopo/reindex", None),
    ])
    async def test_an_unresolved_transaction_blocks_every_entry_point(
            self, main_module, pipeline_calls, method, path, body):
        redis = FakeRedis({"flopo": flopo_entry(update_txn=TXN)})
        main_module.redis_client = redis
        async with client(main_module) as c:
            r = await c.request(method, path, json=body)
        assert r.status_code == 409 and "txn-7" in r.json()["detail"]
        assert pipeline_calls == [] and redis.entry("flopo")["source_url"] == SOURCE

    async def test_webhook_is_blocked_too(self, main_module, pipeline_calls):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry(secret_key="hook", update_txn=TXN)})
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as c:
            r = await c.post("/api/webhook/update/flopo", headers={"X-Webhook-Secret": "hook"})
        assert r.status_code == 409 and pipeline_calls == []

    async def test_trigger_without_source_is_refused(self, main_module, pipeline_calls):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry(source_url=None)})
        async with client(main_module) as c:
            r = await c.post("/admin/ontology/flopo/trigger_update")
        assert r.status_code == 409 and pipeline_calls == []

    async def test_trigger_wait_returns_the_outcome(self, main_module, pipeline_calls):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry()})
        async with client(main_module) as c:
            r = await c.post("/admin/ontology/flopo/trigger_update", params={"wait": "true", "force": "true"})
        assert r.status_code == 200 and r.json()["status"] == "unchanged"
        assert pipeline_calls[0]["force"] is True
        assert pipeline_calls[0]["ontologies_base_path"] == main_module.ONTOLOGIES_DIR

    async def test_reindex_wait_returns_the_outcome(self, main_module, pipeline_calls):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry()})
        async with client(main_module) as c:
            r = await c.post("/admin/ontology/flopo/reindex", params={"wait": "true"})
        assert r.status_code == 200 and r.json()["status"] == "unchanged"

    async def test_trigger_requires_admin(self, main_module, pipeline_calls):
        from httpx import ASGITransport, AsyncClient
        main_module.redis_client = FakeRedis({"flopo": flopo_entry()})
        async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as c:
            r = await c.post("/admin/ontology/flopo/trigger_update")
        assert r.status_code == 401 and pipeline_calls == []

    async def test_check_updates_is_bounded_to_the_selection(self, main_module, pipeline_calls):
        main_module.redis_client = FakeRedis({
            "flopo": flopo_entry(), "go": flopo_entry(ontology="go", ontology_id="go"),
            "po": po_entry(source_url=None), "pato": flopo_entry(ontology="pato", update_txn=TXN),
        })
        async with client(main_module) as c:
            r = await c.post("/admin/check_updates",
                             params={"ontologies": "flopo,po,pato,nope", "wait": "true"})
        body = r.json()
        assert [k["ontology_id"] for k in pipeline_calls] == ["flopo"]
        assert body["unchanged"] == ["flopo"]
        assert body["skipped"] == {"po": "no_source_url", "pato": "recovery_required", "nope": "not_registered"}

    async def test_update_ontologies_bounds_the_default_check(self, main_module, pipeline_calls, monkeypatch):
        monkeypatch.setattr(main_module, "UPDATE_ONTOLOGIES", ["go"])
        main_module.redis_client = FakeRedis({
            "flopo": flopo_entry(), "go": flopo_entry(ontology="go", ontology_id="go")})
        summary = await main_module._check_and_update_all()
        assert [k["ontology_id"] for k in pipeline_calls] == ["go"] and summary["unchanged"] == ["go"]

    async def test_update_status_shows_the_transaction(self, main_module):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry(update_txn=TXN, previous_es_index=V1)})
        async with client(main_module) as c:
            body = (await c.get("/admin/ontology/flopo/update_status")).json()
        assert body["update_txn"]["id"] == "txn-7" and body["previous_es_index"] == V1
        assert "secret_key" not in body and "url" not in body


@pytest.mark.unit
class TestResolve:

    @pytest.fixture
    def setup(self, main_module, monkeypatch):
        redis = FakeRedis({"flopo": flopo_entry(update_txn=TXN, update_status="recovery_required")})
        main_module.redis_client = redis
        state = {"task": "unknown_task"}

        async def once(server_url, task_id):
            state["task"] = state.get("task")
            return state["task"]
        monkeypatch.setattr(updater, "task_status_once", once)
        return main_module, redis, state

    async def post(self, m, body):
        async with client(m) as c:
            return await c.post("/admin/ontology/flopo/resolve_update", json=body)

    async def test_requires_the_transaction_id_and_confirmation(self, setup):
        m, redis, _ = setup
        assert (await self.post(m, {"txn_id": "txn-1", "reconciled": True})).status_code == 409
        assert (await self.post(m, {"txn_id": "txn-7"})).status_code == 422
        assert redis.entry("flopo")["update_txn"]["id"] == "txn-7"

    async def test_refused_while_the_task_is_pending(self, setup):
        m, redis, state = setup
        state["task"] = "pending"
        r = await self.post(m, {"txn_id": "txn-7", "reconciled": True})
        assert r.status_code == 409 and "pending" in r.json()["detail"]
        assert redis.entry("flopo")["update_txn"]["id"] == "txn-7"

    async def test_resolution_removes_only_the_record(self, setup):
        m, redis, _ = setup
        r = await self.post(m, {"txn_id": "txn-7", "reconciled": True, "note": "swap had succeeded; promoted by hand"})
        assert r.status_code == 200
        body = r.json()
        assert body["task_state"] == "unknown_task"
        assert body["left_in_place"]["index"] == "aberowl_flopo_classes_txn-7"
        e = redis.entry("flopo")
        assert "update_txn" not in e and e["update_status"] == "resolved_by_operator"
        assert e["update_history"][-1]["txn"] == "txn-7"
        assert e["source_etag"] == '"v1"', "the next run checks the source again"

    async def test_nothing_to_resolve(self, main_module):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry()})
        async with client(main_module) as c:
            r = await c.post("/admin/ontology/flopo/resolve_update", json={"txn_id": "x", "reconciled": True})
        assert r.status_code == 409


    @pytest.mark.parametrize("body,code", [
        ({"txn_id": "kept", "clear_transfer_cleanup": True}, 422),
        ({"txn_id": "kept", "clear_transfer_cleanup": True, "reconciled": True}, 422),
        ({"txn_id": "wrong", "clear_transfer_cleanup": True, "reconciled": True, "note": "checked"}, 409),
        ({"txn_id": "kept", "clear_transfer_cleanup": True, "reconciled": True, "note": "checked"}, 200),
    ])
    async def test_cleanup_marker_resolution_is_explicit_and_audited(self, main_module, body, code):
        pending = {"txn": "kept", "sha256": "a" * 64, "transfer": "https://host.example"}
        redis = FakeRedis({"flopo": flopo_entry(transfer_release_pending=pending)})
        main_module.redis_client = redis
        async with client(main_module) as c:
            response = await c.post("/admin/ontology/flopo/resolve_update", json=body)
        assert response.status_code == code, response.text
        if code == 200:
            assert response.json()["left_in_place"] == pending
            assert "transfer_release_pending" not in redis.entry("flopo")
            assert redis.entry("flopo")["update_history"][-1]["note"] == "checked"
        else:
            assert redis.entry("flopo")["transfer_release_pending"] == pending

    async def test_cleanup_resolution_cannot_bypass_an_update_transaction(self, main_module):
        main_module.redis_client = FakeRedis({"flopo": flopo_entry(
            update_txn=TXN, transfer_release_pending={"txn": "kept"})})
        async with client(main_module) as c:
            response = await c.post("/admin/ontology/flopo/resolve_update", json={
                "txn_id": "kept", "clear_transfer_cleanup": True, "reconciled": True, "note": "checked"})
        assert response.status_code == 409
        assert main_module.redis_client.entry("flopo")["update_txn"] == TXN


    async def test_cleanup_resolution_holds_the_ontology_update_guard(self, main_module):
        redis = FakeRedis({"flopo": flopo_entry(transfer_release_pending={"txn": "kept"})})
        pipeline = main_module.update_pipeline
        async with pipeline._lock_for("flopo"):
            assert not await pipeline.resolve_transfer_cleanup(redis, "flopo", "kept", "checked")
        assert redis.entry("flopo")["transfer_release_pending"] == {"txn": "kept"}
        assert await pipeline.resolve_transfer_cleanup(redis, "flopo", "kept", "checked")


@pytest.mark.unit
class TestUncertainSharedWorker:
    @pytest.mark.parametrize("kind", ["update", "reindex"])
    @pytest.mark.parametrize("state,phase", [("recovery_required", "validate"), ("running", "index")])
    async def test_other_ontology_cannot_start_heavy_work(self, corpus, worker, es, kind, state, phase):
        pending = {"id": "old-run", "state": state, "phase": phase, "worker": WORKER}
        redis = FakeRedis({"flopo": flopo_entry(update_txn=pending), "po": po_entry()})
        result = await (run(corpus, redis, es, oid="po") if kind == "update" else reindex(corpus, redis, es, oid="po"))
        assert result["error"] == "worker_recovery_required"
        assert "validate" not in worker.kinds() and "index" not in worker.kinds()
        assert "hotswap" not in worker.kinds() and es.calls == []
        assert redis.entry("flopo")["update_txn"] == pending
        assert_clean_end(redis, oid="po")
        assert served(corpus, "po") == OLD

    async def test_other_worker_is_not_quarantined(self, corpus, worker, es):
        pending = {"id": "old-run", "state": "recovery_required", "phase": "validate", "worker": "http://another-worker:8080"}
        redis = FakeRedis({"flopo": flopo_entry(update_txn=pending), "po": po_entry()})
        result = await run(corpus, redis, es, oid="po")
        assert result["success"], result
        assert redis.entry("flopo")["update_txn"] == pending


@pytest.mark.unit
class TestIndexingWait:
    async def test_long_index_can_finish_beyond_the_hotswap_window(self, session, monkeypatch):
        async def skip_sleep(seconds):
            pass
        monkeypatch.setattr(updater, "POLL_INTERVAL", 900)
        monkeypatch.setattr(updater.asyncio, "sleep", skip_sleep)
        pending = _Resp(200, {"status": "running"})
        done = _Resp(200, {"status": "success"})
        short = session(pending, pending, pending, done)
        assert (await updater.poll_task(WORKER,"swap-test"))["status"] == "timeout"
        assert len(short.requests) == 2
        long = session(pending, pending, pending, done)
        result = await updater.poll_task(WORKER,"index-test",10800)
        assert result["status"] == "success" and len(long.requests) == 4

    @pytest.mark.parametrize("kind", ["update", "reindex"])
    async def test_index_wait_is_independent_of_hotswap(self, kind, corpus, worker, es, monkeypatch):
        redis = FakeRedis({"flopo": flopo_entry()})
        calls = []
        async def poll(server, task, timeout_secs=None):
            calls.append((task, timeout_secs))
            return await worker.poll_task(server, task, timeout_secs)
        monkeypatch.setattr(updater, "INDEX_TIMEOUT", 10800)
        monkeypatch.setattr(updater, "poll_task", poll)
        result = await (run(corpus, redis, es, force=True) if kind == "update" else reindex(corpus, redis, es))
        assert result["status"] == "updated"
        assert any(task.startswith("idx") and timeout == 10800 for task,timeout in calls)
        assert all(timeout is None for task,timeout in calls if task.startswith("swap"))
        assert updater.HOTSWAP_TIMEOUT == 1800

    @pytest.mark.parametrize("kind", ["update", "reindex"])
    async def test_invalid_wait_refuses_before_worker_or_registry_changes(self, kind, corpus, worker, es, monkeypatch):
        redis = FakeRedis({"flopo": flopo_entry()})
        before = json.loads(json.dumps(redis._data))
        monkeypatch.setattr(updater, "INDEX_TIMEOUT", None)
        result = await (run(corpus, redis, es, force=True) if kind == "update" else reindex(corpus, redis, es))
        assert result["error"] == "invalid_index_timeout"
        assert worker.events == [] and redis._data == before
