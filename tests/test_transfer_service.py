"""
The ontology transfer service (transfer_service/aberowl_transfer.py), over
real HTTP on a loopback port.

The service is the only writer of a worker host's ontology directory when
central is on another host. These tests drive it as central would and check
the files it leaves: a release becomes visible only whole and verified, the
served file is replaced only while it still holds the expected release,
interrupted operations are completed or refused from what is on disk, and
nothing outside the transaction's own files is touched.
"""

import errno
import json
import os
import socket
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from tests.transfer_helpers import (NEW, OLD, TOKEN, ServiceThread, aberowl_transfer, real, service_config, sha,
                                    snapshot, wait_until, worker_host)

pytestmark = pytest.mark.unit

HAND = b"<rdf:RDF><!-- edited by hand on the worker host --></rdf:RDF>\n"
NEWER = b"<rdf:RDF><!-- flopo release 3 --></rdf:RDF>\n"


@pytest.fixture
def dirs(tmp_path):
    base = real(tmp_path)
    return worker_host(base / "worker-host"), base / "transfer-state"


@pytest.fixture
def start(dirs):
    """Start a service over the same directories; several may run in turn,
    as after a restart."""
    root, state = dirs
    running = []

    def go(**over):
        svc = ServiceThread(aberowl_transfer.create_app(service_config(root, state, **over)))
        svc.__enter__()
        running.append(svc)
        svc.root, svc.state = root, state
        return svc

    yield go
    for svc in running:
        svc.__exit__(None, None, None)


@pytest.fixture
def svc(start):
    return start()


def client(svc, token=TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    # A connection per request: some requests are answered before their body is read.
    return httpx.Client(base_url=svc.url, headers=headers, timeout=30,
                        limits=httpx.Limits(max_keepalive_connections=0))


def txn_url(txn="txn-1", oid="flopo"):
    return f"/v1/ontologies/{oid}/transactions/{txn}"


def prepare(c, txn="txn-1", body=NEW, base=OLD, oid="flopo"):
    return c.put(txn_url(txn, oid), json={"sha256": sha(body), "size": len(body), "base_sha256": sha(base)})


def upload(c, data, txn="txn-1", oid="flopo"):
    return c.put(txn_url(txn, oid) + "/file", content=data)


def promote(c, txn="txn-1", body=NEW, base=OLD, oid="flopo"):
    return c.post(txn_url(txn, oid) + "/promote", json={"sha256": sha(body), "base_sha256": sha(base)})


def finalize(c, txn="txn-1", body=NEW, oid="flopo"):
    return c.post(txn_url(txn, oid) + "/finalize", json={"sha256": sha(body)})


def stage_through(c, txn="txn-1", body=NEW, base=OLD, oid="flopo"):
    assert prepare(c, txn, body, base, oid).status_code == 201
    assert upload(c, body, txn, oid).status_code == 201


def no_links(src, dst):
    """A file system that refuses hard links (EXDEV, or protected_hardlinks)."""
    raise OSError(errno.EXDEV, "Invalid cross-device link")


def journal(svc, txn="txn-1", oid="flopo"):
    path = svc.state / "journal" / oid / f"{txn}.json"
    return json.loads(path.read_text()) if path.exists() else None


def parts(svc, txn="txn-1", oid="flopo"):
    stage_dir = svc.root / oid / "updates" / txn
    return sorted(p.name for p in stage_dir.glob(".upload-*")) if stage_dir.exists() else []


# ---------------------------------------------------------------------------
# Configuration: fail closed
# ---------------------------------------------------------------------------

class TestConfig:

    def write(self, tmp_path, raw, token=TOKEN):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps(raw))
        tok = tmp_path / "token"
        tok.write_text(token + "\n")
        return {"ABEROWL_TRANSFER_CONFIG": str(cfg), "ABEROWL_TRANSFER_TOKEN_FILE": str(tok)}

    def raw(self, dirs, **over):
        root, state = dirs
        state.mkdir(exist_ok=True)
        raw = {"data_root": str(root), "state_dir": str(state), "ontologies": ["flopo"]}
        raw.update(over)
        return raw

    def test_loads_from_the_environment_and_hides_the_token(self, tmp_path, dirs):
        env = self.write(real(tmp_path), self.raw(dirs))
        config = aberowl_transfer.load_config(env)
        assert config.ontologies == {"flopo"} and config.token == TOKEN.encode()
        assert TOKEN not in repr(config)

    @pytest.mark.parametrize("change", [
        {"ontologies": []},
        {"ontologies": ["../flopo"]},
        {"ontologies": ["flopo", "flopo"]},
        {"ontologies": ["go"]},                       # no directory on this host
        {"data_root": "relative/path"},
        {"archive_keep": 0},
        {"max_bytes": True},
        {"worker_data_root": "data"},
        {"worker_data_root": "/data/../etc"},
        {"lock_timeout_seconds": 0},
        {"unexpected": 1},
    ])
    def test_invalid_configuration_is_refused(self, tmp_path, dirs, change):
        env = self.write(real(tmp_path), self.raw(dirs, **change))
        with pytest.raises(aberowl_transfer.ConfigError):
            aberowl_transfer.load_config(env)

    def test_state_inside_the_data_root_is_refused(self, tmp_path, dirs):
        root, _ = dirs
        (root / "state").mkdir()
        env = self.write(real(tmp_path), self.raw(dirs, state_dir=str(root / "state")))
        with pytest.raises(aberowl_transfer.ConfigError, match="separate"):
            aberowl_transfer.load_config(env)

    def test_symlinked_ontology_directory_is_refused(self, tmp_path, dirs):
        root, _ = dirs
        os.symlink(root / "flopo", root / "alias")
        env = self.write(real(tmp_path), self.raw(dirs, ontologies=["alias"]))
        with pytest.raises(aberowl_transfer.ConfigError, match="symlink"):
            aberowl_transfer.load_config(env)

    def test_symlinked_data_root_is_refused(self, tmp_path, dirs):
        root, _ = dirs
        os.symlink(root, real(tmp_path) / "root-link")
        env = self.write(real(tmp_path), self.raw(dirs, data_root=str(real(tmp_path) / "root-link")))
        with pytest.raises(aberowl_transfer.ConfigError, match="symlink"):
            aberowl_transfer.load_config(env)

    @pytest.mark.parametrize("token", ["short", "a" * 20 + " " + "b" * 20])
    def test_weak_token_is_refused(self, tmp_path, dirs, token):
        env = self.write(real(tmp_path), self.raw(dirs), token=token)
        with pytest.raises(aberowl_transfer.ConfigError, match="token"):
            aberowl_transfer.load_config(env)

    def test_missing_or_ambiguous_token_is_refused(self, tmp_path, dirs):
        env = self.write(real(tmp_path), self.raw(dirs))
        without = {k: v for k, v in env.items() if k != "ABEROWL_TRANSFER_TOKEN_FILE"}
        with pytest.raises(aberowl_transfer.ConfigError, match="token"):
            aberowl_transfer.load_config(without)
        with pytest.raises(aberowl_transfer.ConfigError, match="not both"):
            aberowl_transfer.load_config({**env, "ABEROWL_TRANSFER_TOKEN": TOKEN})

    def test_no_configuration_is_refused(self):
        with pytest.raises(aberowl_transfer.ConfigError):
            aberowl_transfer.load_config({})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can write any directory")
    def test_unwritable_ontology_directory_is_refused(self, tmp_path, dirs):
        root, _ = dirs
        os.chmod(root / "flopo", 0o555)
        try:
            env = self.write(real(tmp_path), self.raw(dirs))
            with pytest.raises(aberowl_transfer.ConfigError, match="flopo"):
                aberowl_transfer.load_config(env)
        finally:
            os.chmod(root / "flopo", 0o755)


# ---------------------------------------------------------------------------
# Authentication and paths
# ---------------------------------------------------------------------------

class TestAccess:

    @pytest.mark.parametrize("headers", [
        {}, {"Authorization": "Bearer wrong-token-of-sufficient-length-000000"},
        {"Authorization": f"Basic {TOKEN}"}, {"Authorization": TOKEN},
    ])
    def test_requests_without_the_token_change_nothing(self, svc, headers):
        before = snapshot(svc.root)
        with httpx.Client(base_url=svc.url, headers=headers) as c:
            assert c.get("/v1/ontologies/flopo/served").status_code == 401
            assert prepare(c).status_code == 401
            assert upload(c, NEW).status_code == 401
            assert promote(c).status_code == 401
        assert snapshot(svc.root) == before and journal(svc) is None

    @pytest.mark.parametrize("chunked", [False, True])
    def test_a_large_body_without_the_token_is_refused_unread(self, svc, chunked):
        body = b'{"sha256": "' + b"0" * (2 << 20) + b'"}'
        content = iter([body[:1000], body[1000:]]) if chunked else body
        with httpx.Client(base_url=svc.url, timeout=30, limits=httpx.Limits(max_keepalive_connections=0)) as c:
            r = c.post(txn_url() + "/promote", content=content, headers={"Content-Type": "application/json"})
        assert r.status_code == 401

    @pytest.mark.parametrize("chunked", [False, True])
    def test_json_bodies_are_bounded(self, svc, chunked):
        body = json.dumps({"sha256": sha(NEW), "size": len(NEW), "base_sha256": sha(OLD),
                           "padding": "x" * (aberowl_transfer.JSON_BODY_LIMIT + 1)}).encode()
        content = iter([body[:100], body[100:]]) if chunked else body
        with client(svc) as c:
            r = c.put(txn_url(), content=content, headers={"Content-Type": "application/json"})
            assert r.status_code == 413 and r.json()["error"] == "body_too_large"
            assert prepare(c).status_code == 201, "a body within the limit is accepted"
        assert journal(svc)["state"] == "prepared"

    def test_token_in_the_query_string_is_not_accepted(self, svc):
        with httpx.Client(base_url=svc.url) as c:
            r = c.get("/v1/ontologies/flopo/served", params={"token": TOKEN, "access_token": TOKEN})
        assert r.status_code == 401

    def test_health_needs_no_token_and_says_nothing(self, svc):
        with httpx.Client(base_url=svc.url) as c:
            assert c.get("/health").json() == {"status": "ok"}

    def test_served(self, svc):
        with client(svc) as c:
            body = c.get("/v1/ontologies/po/served").json()
        assert body["sha256"] == sha(OLD) and body["worker_path"] == "/data/po/po.owl"
        assert body["catalogue_dependents"] == ["flopo"] and body["open_transactions"] == []
        assert str(svc.root) not in json.dumps(body), "host paths are not disclosed"

    def test_unconfigured_ontology_is_refused_even_when_on_disk(self, svc):
        before = snapshot(svc.root)
        with client(svc) as c:
            assert c.get("/v1/ontologies/pato/served").status_code == 403
            assert prepare(c, oid="pato").status_code == 403
        assert snapshot(svc.root) == before

    @pytest.mark.parametrize("path", [
        "/v1/ontologies/%2e%2e/served",
        "/v1/ontologies/flopo%2F..%2Fpato/served",
        "/v1/ontologies/flopo/transactions/%2e%2e",
        "/v1/ontologies/flopo/transactions/TXN_1",
        "/v1/ontologies/flopo/transactions/txn-1%2F..%2F..",
    ])
    def test_traversal_is_refused(self, svc, path):
        before = snapshot(svc.root)
        with client(svc) as c:
            r = c.put(path, json={"sha256": sha(NEW), "size": len(NEW), "base_sha256": sha(OLD)})
            g = c.get(path)
        assert r.status_code in (400, 403, 404, 405) and g.status_code in (400, 403, 404, 405)
        assert snapshot(svc.root) == before

    def test_symlinked_served_file_is_refused(self, svc):
        (svc.root / "po" / "po.owl").unlink()
        os.symlink(svc.root / "flopo" / "flopo.owl", svc.root / "po" / "po.owl")
        before = snapshot(svc.root)
        with client(svc) as c:
            assert c.get("/v1/ontologies/po/served").status_code == 409
            assert prepare(c, oid="po").json()["error"] == "unsafe_path"
        assert snapshot(svc.root) == before

    def test_symlinked_updates_directory_is_not_followed(self, svc, tmp_path):
        elsewhere = real(tmp_path) / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, svc.root / "flopo" / "updates")
        with client(svc) as c:
            r = prepare(c)
        assert r.status_code == 409 and r.json()["error"] == "unsafe_path"
        assert list(elsewhere.iterdir()) == []

    def test_an_unowned_stage_directory_is_not_reused(self, svc):
        stray = svc.root / "flopo" / "updates" / "txn-1"
        stray.mkdir(parents=True)
        (stray / "flopo.owl").write_bytes(NEW)
        with client(svc) as c:
            r = prepare(c)
        assert r.status_code == 409 and r.json()["error"] == "unknown_artifact"
        assert (stray / "flopo.owl").read_bytes() == NEW and journal(svc) is None


# ---------------------------------------------------------------------------
# Prepare and upload
# ---------------------------------------------------------------------------

class TestStage:

    def test_stage_is_visible_only_once_verified(self, svc):
        with client(svc) as c:
            r = prepare(c)
            assert r.status_code == 201 and r.json()["state"] == "prepared"
            stage = svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl"
            assert stage.parent.is_dir() and not stage.exists()
            r = upload(c, NEW)
            assert r.status_code == 201 and r.json()["state"] == "staged"
            status = c.get(txn_url()).json()
        assert stage.read_bytes() == NEW and oct(stage.stat().st_mode & 0o777) == "0o644"
        assert status["observed"] == "staged" and status["stage_file"]["sha256"] == sha(NEW)
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == OLD
        assert parts(svc) == []

    def test_prepare_is_idempotent_and_conflicts_are_refused(self, svc):
        with client(svc) as c:
            assert prepare(c).status_code == 201
            assert prepare(c).status_code == 200
            r = prepare(c, body=NEWER)
            assert r.status_code == 409 and r.json()["error"] == "txn_conflict"
            r = prepare(c, txn="txn-2", body=NEWER)
            assert r.status_code == 409 and r.json()["error"] == "other_transaction_open"
        assert journal(svc, "txn-2") is None

    def test_old_base_is_refused(self, svc):
        """Compare and set: central's idea of the served file is stale."""
        with client(svc) as c:
            r = prepare(c, base=HAND)
        assert r.status_code == 409 and r.json()["error"] == "base_mismatch"
        assert r.json()["served_sha256"] == sha(OLD)
        assert journal(svc) is None and not (svc.root / "flopo" / "updates").exists()

    def test_size_and_space_limits(self, start):
        small = start(max_bytes=len(NEW) - 1)
        with client(small) as c:
            assert prepare(c).status_code == 413
        full = start(min_free_bytes=1 << 62)
        with client(full) as c:
            r = prepare(c)
        assert r.status_code == 507 and r.json()["error"] == "insufficient_space"

    def test_corrupted_upload_is_discarded_and_can_be_retried(self, svc):
        corrupt = NEW.replace(b"2", b"9")
        assert len(corrupt) == len(NEW)
        with client(svc) as c:
            prepare(c)
            r = upload(c, corrupt)
            assert r.status_code == 422 and r.json()["error"] == "checksum_mismatch"
            assert not (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").exists()
            assert parts(svc) == [] and journal(svc)["state"] == "prepared"
            assert upload(c, NEW).status_code == 201

    def test_short_and_long_bodies(self, svc):
        with client(svc) as c:
            prepare(c)
            r = upload(c, NEW[:-5])
            assert r.status_code == 400 and r.json()["error"] == "length_mismatch"
            r = c.put(txn_url() + "/file", content=iter([NEW[:10], NEW[10:-5]]))   # chunked
            assert r.status_code == 400 and r.json()["error"] == "truncated"
            r = c.put(txn_url() + "/file", content=iter([NEW, b"extra"]))
            assert r.status_code == 413
        assert parts(svc) == [] and journal(svc)["state"] == "prepared"
        assert not (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").exists()

    def test_connection_lost_mid_upload(self, svc):
        """The client declared the full length and went away half way."""
        with client(svc) as c:
            prepare(c)
        sock = socket.create_connection(("127.0.0.1", svc.port))
        head = (f"PUT {txn_url()}/file HTTP/1.1\r\nHost: test\r\nAuthorization: Bearer {TOKEN}\r\n"
                f"Content-Type: application/octet-stream\r\nContent-Length: {len(NEW)}\r\n\r\n")
        sock.sendall(head.encode() + NEW[:12])
        assert wait_until(lambda: parts(svc) != [])
        sock.close()
        assert wait_until(lambda: parts(svc) == []), "the partial upload is removed"
        assert not (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").exists()
        with client(svc) as c:
            assert c.get(txn_url()).json()["observed"] == "prepared"
            assert upload(c, NEW).status_code == 201

    def test_repeated_upload_keeps_the_stage(self, svc):
        with client(svc) as c:
            stage_through(c)
            inode = (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").stat().st_ino
            assert upload(c, NEW).status_code == 200
            assert prepare(c).json()["state"] == "staged"
        assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").stat().st_ino == inode

    def test_a_stage_installed_before_its_journal_entry_is_recorded_as_staged(self, svc, monkeypatch):
        """Interrupted between linking the verified upload and journalling
        it: the next request records the stage durably before reporting it."""
        real_write = aberowl_transfer.TransferService._write
        failed = []

        def interrupted(self, record):
            if record.get("state") == "staged" and not record.get("settled_after_interruption") and not failed:
                failed.append(record["txn"])
                raise OSError(5, "Input/output error")
            real_write(self, record)
        monkeypatch.setattr(aberowl_transfer.TransferService, "_write", interrupted)
        with client(svc) as c:
            prepare(c)
            assert upload(c, NEW).status_code == 500
            assert journal(svc)["state"] == "prepared"
            assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").read_bytes() == NEW
            status = c.get(txn_url()).json()
        assert status["state"] == "staged" and status["observed"] == "staged"
        assert journal(svc)["state"] == "staged" and journal(svc)["settled_after_interruption"] is True

    def test_unknown_transaction(self, svc):
        with client(svc) as c:
            assert c.get(txn_url()).json()["error"] == "unknown_transaction"
            assert upload(c, NEW).status_code == 404
            assert promote(c).status_code == 404


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------

class TestPromote:

    def test_promotion_archives_and_keeps_the_stage(self, svc):
        root = svc.root
        with client(svc) as c:
            stage_through(c)
            r = promote(c)
            assert r.status_code == 200 and r.json()["state"] == "promoted"
            assert r.json()["archive"] == "flopo/archive/flopo_txn-1.owl"
            assert c.get(txn_url()).json()["observed"] == "promoted"
            again = promote(c)
        served, stage = root / "flopo" / "flopo.owl", root / "flopo" / "updates" / "txn-1" / "flopo.owl"
        assert served.read_bytes() == NEW and stage.read_bytes() == NEW, "the worker reads the stage until finalize"
        assert served.stat().st_ino == stage.stat().st_ino
        assert (root / "flopo" / "archive" / "flopo_txn-1.owl").read_bytes() == OLD
        assert again.status_code == 200 and os.listdir(root / "flopo" / "archive") == ["flopo_txn-1.owl"]
        assert not list((root / "flopo").glob(".flopo.owl.promote-*"))

    def test_catalogue_dependents_come_from_the_worker_host(self, svc):
        with client(svc) as c:
            stage_through(c, oid="po")
            assert promote(c, oid="po").json()["catalogue_dependents"] == ["flopo"]

    def test_a_served_file_changed_since_prepare_is_never_overwritten(self, svc):
        with client(svc) as c:
            stage_through(c)
            (svc.root / "flopo" / "flopo.owl").write_bytes(HAND)
            r = promote(c)
            assert r.status_code == 409 and r.json()["error"] == "served_changed"
            assert c.get(txn_url()).json()["observed"] == "conflict"
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == HAND
        assert not (svc.root / "flopo" / "archive").exists() and journal(svc)["state"] == "staged"
        assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").read_bytes() == NEW

    def test_promote_needs_a_stage(self, svc):
        with client(svc) as c:
            prepare(c)
            assert promote(c).json()["error"] == "wrong_state"
            assert promote(c, base=HAND).json()["error"] == "txn_conflict"
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == OLD

    def test_interrupted_before_the_replace_is_resumed(self, svc, monkeypatch):
        real_replace = aberowl_transfer._replace
        calls = []

        def crash_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise OSError(5, "Input/output error")
            real_replace(src, dst)
        monkeypatch.setattr(aberowl_transfer, "_replace", crash_once)
        with client(svc) as c:
            stage_through(c)
            assert promote(c).status_code == 500
            status = c.get(txn_url()).json()
            assert status["state"] == "promoting" and status["observed"] == "staged"
            assert (svc.root / "flopo" / "flopo.owl").read_bytes() == OLD
            assert promote(c).status_code == 200
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == NEW
        assert os.listdir(svc.root / "flopo" / "archive") == ["flopo_txn-1.owl"]
        assert (svc.root / "flopo" / "archive" / "flopo_txn-1.owl").read_bytes() == OLD

    def test_interrupted_after_the_replace_is_confirmed_after_a_restart(self, start, monkeypatch):
        first = start()
        real_promoted = aberowl_transfer.TransferService._promoted

        def crash(self, record, paths, served):
            raise RuntimeError("power cut before the journal was written")
        monkeypatch.setattr(aberowl_transfer.TransferService, "_promoted", crash)
        with client(first) as c:
            stage_through(c)
            assert promote(c).status_code == 500
        assert (first.root / "flopo" / "flopo.owl").read_bytes() == NEW
        assert journal(first)["state"] == "promoting"

        monkeypatch.setattr(aberowl_transfer.TransferService, "_promoted", real_promoted)
        first.__exit__(None, None, None)
        second = start()
        with client(second) as c:
            assert c.get(txn_url()).json()["observed"] == "promoted"
            r = promote(c)
        assert r.status_code == 200 and journal(second)["state"] == "promoted"
        assert os.listdir(second.root / "flopo" / "archive") == ["flopo_txn-1.owl"]

    def test_without_hard_links_the_files_are_verified_copies(self, svc, monkeypatch):
        monkeypatch.setattr(aberowl_transfer, "_link", no_links)
        with client(svc) as c:
            stage_through(c)
            assert promote(c).status_code == 200
            assert c.get(txn_url()).json()["observed"] == "promoted"
            assert promote(c).status_code == 200
            served = svc.root / "flopo" / "flopo.owl"
            stage = svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl"
            assert served.read_bytes() == NEW and served.stat().st_ino != stage.stat().st_ino
            assert journal(svc)["promote_file"][1] == served.stat().st_ino
            assert (svc.root / "flopo" / "archive" / "flopo_txn-1.owl").read_bytes() == OLD
            assert finalize(c).status_code == 200
        assert not (svc.root / "flopo" / "updates").exists()
        assert not [p for p in (svc.root / "flopo").iterdir() if p.name.startswith(".")]

    def test_no_space_for_a_copy_is_a_definite_refusal(self, svc, monkeypatch):
        with client(svc) as c:
            stage_through(c)
            monkeypatch.setattr(aberowl_transfer, "_link", no_links)
            monkeypatch.setattr(aberowl_transfer.shutil, "disk_usage",
                                lambda path: SimpleNamespace(total=1 << 30, used=1 << 30, free=0))
            r = promote(c)
            assert r.status_code == 507 and r.json()["error"] == "insufficient_space"
            monkeypatch.undo()
            assert c.get(txn_url()).json()["observed"] == "staged"
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == OLD

    def test_identical_release_without_links_is_not_confirmed_by_content_alone(self, svc, monkeypatch):
        """A forced update of an unchanged file, on a file system without
        hard links, interrupted before the replace: equal content must not
        pass for a promotion."""
        monkeypatch.setattr(aberowl_transfer, "_link", no_links)
        real_replace = aberowl_transfer._replace
        calls = []

        def fail_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise OSError(5, "Input/output error")
            real_replace(src, dst)
        monkeypatch.setattr(aberowl_transfer, "_replace", fail_once)
        served = svc.root / "flopo" / "flopo.owl"
        before = served.stat().st_ino
        with client(svc) as c:
            stage_through(c, body=OLD, base=OLD)
            assert promote(c, body=OLD, base=OLD).status_code == 500
            assert served.stat().st_ino == before
            status = c.get(txn_url()).json()
            assert status["state"] == "promoting" and status["observed"] == "staged"
            assert finalize(c, body=OLD).status_code == 409, "never closed before the replace"
            assert promote(c, body=OLD, base=OLD).status_code == 200
        assert served.stat().st_ino != before and journal(svc)["promote_file"][1] == served.stat().st_ino

    def test_identical_release_without_links_interrupted_after_the_replace(self, svc, monkeypatch):
        monkeypatch.setattr(aberowl_transfer, "_link", no_links)
        real_promoted = aberowl_transfer.TransferService._promoted
        crashed = []

        def crash_once(self, record, paths, served):
            if not crashed:
                crashed.append(1)
                raise RuntimeError("power cut before the journal was written")
            return real_promoted(self, record, paths, served)
        monkeypatch.setattr(aberowl_transfer.TransferService, "_promoted", crash_once)
        with client(svc) as c:
            stage_through(c, body=OLD, base=OLD)
            assert promote(c, body=OLD, base=OLD).status_code == 500
            assert journal(svc)["state"] == "promoting"
            assert c.get(txn_url()).json()["observed"] == "promoted"
            assert promote(c, body=OLD, base=OLD).status_code == 200
            # A lost answer after that: the journal now records the replace.
            assert promote(c, body=OLD, base=OLD).status_code == 200
            assert c.get(txn_url()).json()["observed"] == "promoted"
        assert journal(svc)["state"] == "promoted"

    def test_identical_release_is_promoted_by_identity(self, svc):
        """A forced update with unchanged content: the hashes alone cannot
        tell old from new, the inode can."""
        with client(svc) as c:
            stage_through(c, body=OLD, base=OLD)
            assert promote(c, body=OLD, base=OLD).status_code == 200
            assert c.get(txn_url()).json()["observed"] == "promoted"
        served = svc.root / "flopo" / "flopo.owl"
        assert served.stat().st_ino == (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").stat().st_ino


# ---------------------------------------------------------------------------
# Finalize and abort: only the transaction's own files
# ---------------------------------------------------------------------------

class TestCleanup:

    def test_finalize_removes_only_the_stage(self, svc):
        with client(svc) as c:
            stage_through(c)
            promote(c)
            r = finalize(c)
            assert r.status_code == 200 and r.json()["state"] == "finalized"
            assert finalize(c).status_code == 200
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == NEW
        assert not (svc.root / "flopo" / "updates").exists()
        assert (svc.root / "flopo" / "archive" / "flopo_txn-1.owl").read_bytes() == OLD

    def test_finalize_before_promotion_keeps_the_stage(self, svc):
        with client(svc) as c:
            stage_through(c)
            assert finalize(c).status_code == 409
        assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").read_bytes() == NEW

    def test_finalize_after_a_later_change_keeps_the_stage(self, svc):
        with client(svc) as c:
            stage_through(c)
            promote(c)
            (svc.root / "flopo" / "flopo.owl").unlink()
            (svc.root / "flopo" / "flopo.owl").write_bytes(HAND)
            r = finalize(c)
        assert r.status_code == 409 and r.json()["error"] == "served_changed"
        assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").read_bytes() == NEW

    def test_abort_removes_the_stage_and_leaves_the_served_file(self, svc):
        with client(svc) as c:
            stage_through(c)
            notes = svc.root / "flopo" / "updates" / "txn-1" / "operator-notes.txt"
            notes.write_text("kept")
            r = c.post(txn_url() + "/abort")
            assert r.status_code == 200 and r.json()["left_in_place"] == ["flopo/updates/txn-1/operator-notes.txt"]
            assert c.post(txn_url() + "/abort").status_code == 200
            # A finished transaction no longer blocks the next one.
            assert prepare(c, txn="txn-2").status_code == 201
        assert notes.read_text() == "kept"
        assert not (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").exists()
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == OLD

    def test_a_promoted_transaction_blocks_the_next_until_finalized(self, svc):
        """The worker may read a promoted stage until central finalizes it,
        so a later transaction cannot start and strand it."""
        with client(svc) as c:
            stage_through(c)
            promote(c)
            assert c.get("/v1/ontologies/flopo/served").json()["open_transactions"] == ["txn-1"]
            r = prepare(c, txn="txn-2", body=NEWER, base=NEW)
            assert r.status_code == 409 and r.json()["transactions"] == ["txn-1"]
            assert finalize(c).status_code == 200
            assert prepare(c, txn="txn-2", body=NEWER, base=NEW).status_code == 201

    def test_an_operator_closes_a_promotion_after_a_manual_rollback(self, svc):
        with client(svc) as c:
            stage_through(c)
            assert c.post(txn_url() + "/finalize",
                          json={"sha256": sha(NEW), "served_changed_ok": True}).status_code == 409, "not promoted yet"
            promote(c)
            served = svc.root / "flopo" / "flopo.owl"
            (svc.root / "flopo" / ".rollback").write_bytes(HAND)
            os.replace(svc.root / "flopo" / ".rollback", served)
            assert finalize(c).status_code == 409
            r = c.post(txn_url() + "/finalize", json={"sha256": sha(NEW), "served_changed_ok": True})
            assert r.status_code == 200 and r.json()["closed_with_served_changed"]
            assert prepare(c, txn="txn-2", body=NEWER, base=HAND).status_code == 201
        assert served.read_bytes() == HAND and not (svc.root / "flopo" / "updates" / "txn-1").exists()

    def test_a_promotion_is_never_aborted(self, svc):
        with client(svc) as c:
            stage_through(c)
            promote(c)
            r = c.post(txn_url() + "/abort")
        assert r.status_code == 409 and r.json()["error"] == "cannot_abort"
        assert (svc.root / "flopo" / "flopo.owl").read_bytes() == NEW
        assert (svc.root / "flopo" / "updates" / "txn-1" / "flopo.owl").exists()

    def test_archives_of_finished_transactions_are_pruned(self, start):
        svc = start(archive_keep=1)
        operator_file = svc.root / "flopo" / "archive" / "flopo_20250101T000000.owl"
        operator_file.parent.mkdir()
        operator_file.write_bytes(b"kept by the operator")
        with client(svc) as c:
            stage_through(c)
            promote(c)
            finalize(c)
            stage_through(c, txn="txn-2", body=NEWER, base=NEW)
            promote(c, txn="txn-2", body=NEWER, base=NEW)
            r = finalize(c, txn="txn-2", body=NEWER)
        assert r.json()["pruned_archives"] == ["flopo/archive/flopo_txn-1.owl"]
        names = sorted(os.listdir(svc.root / "flopo" / "archive"))
        assert names == ["flopo_20250101T000000.owl", "flopo_txn-2.owl"]
        assert (svc.root / "flopo" / "archive" / "flopo_txn-2.owl").read_bytes() == NEW

    def test_a_full_cycle_touches_nothing_else(self, svc):
        before = snapshot(svc.root)
        with client(svc) as c:
            stage_through(c)
            promote(c)
            finalize(c)
        after = snapshot(svc.root)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        assert changed == {"flopo/flopo.owl", "flopo/archive/flopo_txn-1.owl"}
        assert after["flopo/flopo.owl"] == sha(NEW)


# ---------------------------------------------------------------------------
# Locks are shared between processes
# ---------------------------------------------------------------------------

def test_another_process_holding_the_lock_blocks_writes(svc):
    lock = svc.state / "locks" / "flopo.lock"
    code = ("import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('locked', flush=True)\n"
            "sys.stdin.read()\n")
    holder = subprocess.Popen([sys.executable, "-c", code, str(lock)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        with client(svc) as c:
            r = prepare(c)
            assert r.status_code == 503 and r.json()["error"] == "busy"
            assert journal(svc) is None
            holder.stdin.close()
            holder.wait(10)
            assert prepare(c).status_code == 201
    finally:
        if holder.poll() is None:
            holder.kill()
