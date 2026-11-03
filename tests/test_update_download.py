"""
The update pipeline's download (updater.download_ontology): streamed in
chunks, gzip removed incrementally whether sent as Content-Encoding or as a
.gz file, both sizes bounded, short or corrupt bodies refused, and the file
staged only when complete. A large, highly compressible body checks that
memory stays bounded.
"""

import asyncio
import gzip
import hashlib
import io
import stat
import struct
import zipfile
import sys
import tracemalloc
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "central_server"))

from app.intake import updater  # noqa: E402

pytestmark = pytest.mark.unit

OWL = b"<rdf:RDF><!-- release --></rdf:RDF>\n" * 1000
URL = "https://purl.obolibrary.org/obo/flopo.owl"


class _Content:
    def __init__(self, body, chunk, fail_after=None):
        self.body, self.chunk, self.fail_after = body, chunk, fail_after
        self.read_sizes = []

    async def iter_chunked(self, n):
        self.read_sizes.append(n)
        body = self.body() if callable(self.body) else iter([self.body])
        sent = 0
        for block in body:
            for i in range(0, len(block), self.chunk):
                piece = block[i:i + self.chunk]
                sent += len(piece)
                if self.fail_after is not None and sent > self.fail_after:
                    raise ConnectionResetError("connection reset")
                yield piece
                await asyncio.sleep(0)


class _Resp:
    def __init__(self, body, status=200, headers=None, chunk=65536, fail_after=None):
        self.status = status
        self.headers = headers or {}
        self.content = _Content(body, chunk, fail_after)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.requests = []

    def get(self, url, **kw):
        self.requests.append((url, kw))
        return self.resp


async def download(tmp_path, resp):
    session = _Session(resp)
    dest = tmp_path / "flopo" / "updates" / "txn-1" / "flopo.owl"
    result = await updater.download_ontology(URL, str(dest), session, headers={"Authorization": "apikey token=k"})
    return result, dest, session


def leftovers(dest):
    return sorted(p.name for p in dest.parent.iterdir()) if dest.parent.exists() else []


class TestDecoding:

    async def test_plain(self, tmp_path):
        result, dest, session = await download(tmp_path, _Resp(OWL, headers={"Content-Length": str(len(OWL))}))
        assert dest.read_bytes() == OWL and result["md5"] == hashlib.md5(OWL).hexdigest()
        assert result["size"] == len(OWL) == result["received"]
        _, kw = session.requests[0]
        assert kw["headers"] == {"Authorization": "apikey token=k", "Accept-Encoding": "gzip"}
        assert kw["timeout"].total == 3600 and kw["allow_redirects"] is True

    @pytest.mark.parametrize("headers", [{"Content-Encoding": "gzip"}, {"Content-Type": "application/gzip"}, {}])
    async def test_gzip_by_content_not_headers(self, tmp_path, headers):
        result, dest, _ = await download(tmp_path, _Resp(gzip.compress(OWL), headers=headers))
        assert dest.read_bytes() == OWL and result["size"] == len(OWL)

    async def test_gz_file_sent_with_content_encoding_has_two_layers(self, tmp_path):
        body = gzip.compress(gzip.compress(OWL))
        result, dest, _ = await download(tmp_path, _Resp(body, headers={"Content-Encoding": "gzip"}))
        assert dest.read_bytes() == OWL

    async def test_concatenated_members_and_zero_padding(self, tmp_path):
        body = gzip.compress(OWL[:500]) + gzip.compress(OWL[500:]) + b"\0" * 64
        result, dest, _ = await download(tmp_path, _Resp(body, chunk=7))
        assert dest.read_bytes() == OWL

    async def test_tiny_bodies(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(b"x"))
        assert dest.read_bytes() == b"x"


class TestRefusals:

    @staticmethod
    def bad_crc():
        body = bytearray(gzip.compress(OWL))
        body[-8] ^= 0xFF  # the CRC-32 of the trailer
        return bytes(body)

    @pytest.mark.parametrize("body,fragment", [
        (gzip.compress(OWL)[:-12], "truncated gzip"),
        (bad_crc.__func__(), "corrupt gzip"),
        (gzip.compress(OWL) + b"trailing garbage", "gzip"),
    ])
    async def test_bad_gzip(self, tmp_path, body, fragment):
        result, dest, _ = await download(tmp_path, _Resp(body))
        assert fragment in result["error"]
        assert not dest.exists() and leftovers(dest) == []

    async def test_shorter_than_content_length(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(OWL, headers={"Content-Length": str(len(OWL) + 10)}))
        assert "truncated" in result["error"] and leftovers(dest) == []

    async def test_longer_than_content_length(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(OWL, headers={"Content-Length": "100"}))
        assert "Content-Length" in result["error"] and leftovers(dest) == []

    async def test_declared_size_over_the_limit_is_not_read(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_DOWNLOAD_BYTES", 1000)
        resp = _Resp(OWL, headers={"Content-Length": str(len(OWL))})
        result, dest, _ = await download(tmp_path, resp)
        assert "over the limit" in result["error"] and resp.content.read_sizes == []

    async def test_received_bytes_are_bounded_without_content_length(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_DOWNLOAD_BYTES", 1000)
        result, dest, _ = await download(tmp_path, _Resp(OWL))
        assert "received" in result["error"] and leftovers(dest) == []

    async def test_other_content_encoding(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(zlib.compress(OWL), headers={"Content-Encoding": "br"}))
        assert "Content-Encoding" in result["error"] and leftovers(dest) == []

    async def test_connection_lost_removes_only_its_own_partial_file(self, tmp_path):
        dest = tmp_path / "flopo" / "updates" / "txn-1" / "flopo.owl"
        dest.parent.mkdir(parents=True)
        other = dest.parent / ".flopo.owl.someone-else.part"
        other.write_bytes(b"not ours")
        result, _, _ = await download(tmp_path, _Resp(OWL, fail_after=5000, chunk=1000))
        assert "reset" in result["error"]
        assert leftovers(dest) == [other.name] and other.read_bytes() == b"not ours"

    async def test_http_error(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(b"", status=404))
        assert result == {"error": "HTTP 404"}


async def test_an_invalid_limit_refuses_every_download(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "MAX_EXPANDED_BYTES", None)
    result, dest, session = await download(tmp_path, _Resp(OWL))
    assert "UPDATE_MAX" in result["error"] and session.requests == []


# ---------------------------------------------------------------------------
# Real aiohttp against a loopback server
# ---------------------------------------------------------------------------

def raw_response(body, headers=None, length="exact"):
    """An HTTP/1.1 response as bytes. length: "exact", None (no
    Content-Length: the body ends when the connection closes) or a number."""
    head = ["HTTP/1.1 200 OK", "Connection: close"] + [f"{k}: {v}" for k, v in (headers or {}).items()]
    if length == "exact":
        length = len(body)
    if length is not None:
        head.append(f"Content-Length: {length}")
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


class RawServer:
    """A loopback server that answers each path with fixed bytes and closes
    the connection, so lengths and truncation are exactly as written."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    async def _handle(self, reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        self.requests.append(request.decode("latin-1"))
        path = request.split(b" ", 2)[1].decode()
        writer.write(self.routes[path])
        await writer.drain()
        writer.close()


GZ = gzip.compress(OWL)


class TestRealClient:

    async def real(self, tmp_path, server, path, auto_decompress=False):
        import aiohttp
        dest = tmp_path / "flopo" / "updates" / "txn-1" / "flopo.owl"
        async with aiohttp.ClientSession(auto_decompress=auto_decompress) as session:
            result = await updater.download_ontology(server.url(path), str(dest), session)
        return result, dest

    async def test_content_encoding_gzip_arrives_raw_and_is_decoded_once(self, tmp_path):
        async with RawServer({"/o": raw_response(GZ, {"Content-Encoding": "gzip"})}) as server:
            result, dest = await self.real(tmp_path, server, "/o")
        assert dest.read_bytes() == OWL and result["received"] == len(GZ) and result["size"] == len(OWL)
        assert "accept-encoding: gzip\r\n" in server.requests[0].lower()

    async def test_a_decoding_session_is_caught_by_the_length_check(self, tmp_path):
        """Why _update_txn makes its session with auto_decompress=False: a
        decoding client hands over more bytes than Content-Length says."""
        async with RawServer({"/o": raw_response(GZ, {"Content-Encoding": "gzip"})}) as server:
            result, dest = await self.real(tmp_path, server, "/o", auto_decompress=True)
        assert "Content-Length" in result["error"] and leftovers(dest) == []

    async def test_truncated_body(self, tmp_path):
        async with RawServer({"/o": raw_response(GZ, {"Content-Encoding": "gzip"}, length=len(GZ) + 50)}) as server:
            result, dest = await self.real(tmp_path, server, "/o")
        assert "error" in result and not dest.exists() and leftovers(dest) == []

    async def test_received_cap_without_content_length(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_DOWNLOAD_BYTES", 1000)
        async with RawServer({"/o": raw_response(OWL, length=None)}) as server:
            result, dest = await self.real(tmp_path, server, "/o")
        assert "received" in result["error"] and leftovers(dest) == []

    async def test_expanded_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_EXPANDED_BYTES", 1000)
        async with RawServer({"/o": raw_response(gzip.compress(b"\0" * (1 << 20)),
                                                 {"Content-Encoding": "gzip"})}) as server:
            result, dest = await self.real(tmp_path, server, "/o")
        assert "decompressed" in result["error"] and leftovers(dest) == []

    async def test_other_encoding(self, tmp_path):
        async with RawServer({"/o": raw_response(b"xxxx", {"Content-Encoding": "br"})}) as server:
            result, dest = await self.real(tmp_path, server, "/o")
        assert "Content-Encoding" in result["error"]

    async def test_the_pipeline_downloads_with_client_decoding_off(self, tmp_path, monkeypatch):
        """_update_txn's own session, end to end: a gzip-encoded release is
        staged, validated and swapped in as plain OWL."""
        from tests.test_update_pipeline import NEW, OLD, FakeES, FakeRedis, FakeWorker, flopo_entry
        es = FakeES()
        w = FakeWorker(es)
        for name, fn in (("worker_served_path", w.worker_served_path), ("check_version", w.check_version),
                         ("validate_staged", w.validate), ("discard_export", w.discard),
                         ("start_indexing", w.start_indexing), ("start_hotswap", w.start_hotswap),
                         ("poll_task", w.poll_task), ("finalize_served_path", w.finalize)):
            monkeypatch.setattr(updater, name, fn)
        monkeypatch.setattr(updater, "_locks", {})
        monkeypatch.setattr(updater, "_worker_locks", {})
        monkeypatch.delenv("UPDATE_TRANSFER_CONFIG", raising=False)
        (tmp_path / "flopo").mkdir()
        (tmp_path / "flopo" / "flopo.owl").write_bytes(OLD)
        async with RawServer({"/flopo.owl": raw_response(gzip.compress(NEW), {"Content-Encoding": "gzip"})}) as srv:
            redis = FakeRedis({"flopo": flopo_entry(source_url=srv.url("/flopo.owl"))})
            result = await updater.execute_update_pipeline(
                ontology_id="flopo", registry_entry=redis.entry("flopo"), redis_client=redis, es_mgr=es,
                ontologies_base_path=str(tmp_path), es_url="")
        assert result["status"] == "updated", result
        assert (tmp_path / "flopo" / "flopo.owl").read_bytes() == NEW


class TestBounds:

    def zeros_gz(self, size):
        """A gzip stream of size zero bytes, produced and compressed in
        pieces so the test itself never holds it whole."""
        def body():
            c = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
            block = b"\0" * (1 << 20)
            for _ in range(size // len(block)):
                out = c.compress(block)
                if out:
                    yield out
            yield c.flush()
        return body

    async def test_expanded_size_is_bounded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_EXPANDED_BYTES", 5 << 20)
        result, dest, _ = await download(tmp_path, _Resp(self.zeros_gz(64 << 20)))
        assert "decompressed" in result["error"] and leftovers(dest) == []

    def test_decoder_output_chunks_are_bounded(self):
        decoder = updater._Decoder()
        sizes = []
        for piece in self.zeros_gz(32 << 20)():
            sizes.extend(len(out) for out in decoder.feed(piece))
        sizes.extend(len(out) for out in decoder.close())
        assert sum(sizes) == 32 << 20 and max(sizes) <= updater.DOWNLOAD_CHUNK

    async def test_memory_stays_bounded_for_a_large_compressible_body(self, tmp_path):
        size = 128 << 20
        tracemalloc.start()
        try:
            result, dest, _ = await download(tmp_path, _Resp(self.zeros_gz(size), chunk=65536))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert result["size"] == size and dest.stat().st_size == size
        assert peak < 16 << 20, f"peak traced memory {peak} bytes for a {size} byte download"


# ---------------------------------------------------------------------------
# Single-file ZIP packages (with existing gzip and transaction handling)
# ---------------------------------------------------------------------------

def ontology_zip(payload=OWL, name="dcm.owl", compression=zipfile.ZIP_DEFLATED,
                 extra=None, mode=None, timestamp=(2026, 1, 1, 0, 0, 0)):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        member = zipfile.ZipInfo(name, date_time=timestamp)
        member.compress_type = compression
        if mode is not None:
            member.create_system = 3
            member.external_attr = mode << 16
        archive.writestr(member, payload)
        if extra is not None:
            archive.writestr("second.owl", extra)
    return stream.getvalue()


class TestZipDownloads:
    @pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
    @pytest.mark.parametrize("wrap_gzip", [False, True])
    async def test_package_is_staged_as_ontology_and_hashed_after_unpacking(self, tmp_path, compression, wrap_gzip):
        body = ontology_zip(compression=compression)
        if wrap_gzip:
            body = gzip.compress(body)
        result, dest, _ = await download(tmp_path, _Resp(body, chunk=7))
        assert result == {"md5": hashlib.md5(OWL).hexdigest(), "size": len(OWL), "received": len(body)}
        assert dest.read_bytes() == OWL and leftovers(dest) == [dest.name]

    async def test_packaging_metadata_does_not_change_the_source_hash(self, tmp_path):
        first = ontology_zip(timestamp=(2025, 1, 1, 0, 0, 0))
        second = ontology_zip(timestamp=(2026, 1, 1, 0, 0, 0))
        assert first != second
        a, dest, _ = await download(tmp_path, _Resp(first))
        b, _, _ = await download(tmp_path, _Resp(second))
        assert a["md5"] == b["md5"] == hashlib.md5(OWL).hexdigest()
        assert dest.read_bytes() == OWL

    async def test_relative_member_path_does_not_create_directories(self, tmp_path):
        result, dest, _ = await download(tmp_path, _Resp(ontology_zip(name="release/dcm.owl")))
        assert "error" not in result and dest.read_bytes() == OWL
        assert not (dest.parent / "release").exists()

    @pytest.mark.parametrize("body,fragment", [
        (ontology_zip(extra=b"second ontology"), "exactly one"),
        (ontology_zip(name="../escape.owl"), "safe relative"),
        (ontology_zip(name="/absolute.owl"), "safe relative"),
        (ontology_zip(name="C:/drive.owl"), "safe relative"),
        (ontology_zip(name="dir\\escape.owl"), "safe relative"),
        (ontology_zip(name="directory/", mode=stat.S_IFDIR | 0o755), "regular file"),
        (ontology_zip(payload=b"target", mode=stat.S_IFLNK | 0o777), "regular file"),
        (ontology_zip(payload=gzip.compress(OWL)), "nested"),
        (ontology_zip(payload=ontology_zip()), "nested"),
        (ontology_zip(compression=zipfile.ZIP_BZIP2), "compression method"),
        (ontology_zip()[:-10], "ZIP"),
        (ontology_zip() + ontology_zip(), "central directory bounds"),
    ])
    async def test_invalid_archive_preserves_served_file_and_removes_only_its_partials(self, tmp_path, body, fragment):
        dest = tmp_path / "flopo" / "updates" / "txn-1" / "flopo.owl"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"served source")
        other = dest.parent / ".another-update.part"
        other.write_bytes(b"another transaction")
        result, _, _ = await download(tmp_path, _Resp(body))
        assert fragment in result["error"]
        assert dest.read_bytes() == b"served source" and other.read_bytes() == b"another transaction"
        assert leftovers(dest) == sorted([dest.name, other.name])
        assert not (dest.parent.parent / "escape.owl").exists()

    async def test_bad_member_crc(self, tmp_path):
        body = bytearray(ontology_zip(compression=zipfile.ZIP_STORED))
        offset = body.find(OWL)
        assert offset >= 0
        body[offset] ^= 1
        result, dest, _ = await download(tmp_path, _Resp(bytes(body)))
        assert "ZIP" in result["error"] and leftovers(dest) == []

    async def test_encrypted_flag_is_refused(self, tmp_path):
        body = bytearray(ontology_zip())
        central = body.index(b"PK\x01\x02")
        body[6] |= 1
        body[central + 8] |= 1
        result, dest, _ = await download(tmp_path, _Resp(bytes(body)))
        assert "encrypted" in result["error"] and leftovers(dest) == []

    async def test_expanded_payload_is_bounded_even_if_package_fits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(updater, "MAX_EXPANDED_BYTES", 4096)
        body = ontology_zip(payload=b"x" * (1 << 20))
        assert len(body) < 4096
        result, dest, _ = await download(tmp_path, _Resp(body))
        assert "decompressed from ZIP" in result["error"] and leftovers(dest) == []

    async def test_metadata_bound_is_checked_before_zipfile_allocates_directory(self, tmp_path, monkeypatch):
        body = bytearray(ontology_zip())
        end = body.rindex(b"PK\x05\x06")
        struct.pack_into("<L", body, end + 12, 1 << 28)
        def forbidden(*args, **kwargs):
            pytest.fail("unbounded directory passed to ZipFile")
        monkeypatch.setattr(updater.zipfile, "ZipFile", forbidden)
        result, dest, _ = await download(tmp_path, _Resp(bytes(body)))
        assert "central directory exceeds" in result["error"] and leftovers(dest) == []

    async def test_empty_archive_is_refused(self, tmp_path):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w"):
            pass
        result, dest, _ = await download(tmp_path, _Resp(stream.getvalue()))
        assert "exactly one" in result["error"] and leftovers(dest) == []

    async def test_real_client_can_decode_gzip_wrapped_zip(self, tmp_path):
        body = gzip.compress(ontology_zip())
        async with RawServer({"/o": raw_response(body, {"Content-Encoding": "gzip"})}) as server:
            result, dest = await TestRealClient().real(tmp_path, server, "/o")
        assert result["md5"] == hashlib.md5(OWL).hexdigest()
        assert result["received"] == len(body) and dest.read_bytes() == OWL

    async def test_pipeline_stages_plain_ontology_and_skips_repackaged_unchanged_source(self, tmp_path, monkeypatch):
        from tests.test_update_pipeline import NEW, OLD, FakeES, FakeRedis, FakeWorker, flopo_entry
        es = FakeES()
        worker = FakeWorker(es)
        for name, fn in (("worker_served_path", worker.worker_served_path), ("check_version", worker.check_version),
                         ("validate_staged", worker.validate), ("discard_export", worker.discard),
                         ("start_indexing", worker.start_indexing), ("start_hotswap", worker.start_hotswap),
                         ("poll_task", worker.poll_task), ("finalize_served_path", worker.finalize)):
            monkeypatch.setattr(updater, name, fn)
        monkeypatch.setattr(updater, "_locks", {})
        monkeypatch.setattr(updater, "_worker_locks", {})
        monkeypatch.delenv("UPDATE_TRANSFER_CONFIG", raising=False)
        (tmp_path / "flopo").mkdir()
        (tmp_path / "flopo/flopo.owl").write_bytes(OLD)
        async with RawServer({"/o": raw_response(ontology_zip(NEW))}) as server:
            redis = FakeRedis({"flopo": flopo_entry(source_url=server.url("/o"))})
            result = await updater.execute_update_pipeline("flopo", redis.entry("flopo"), redis, es, str(tmp_path), "")
            assert result["status"] == "updated", result
            assert (tmp_path / "flopo/flopo.owl").read_bytes() == NEW
            assert redis.entry("flopo")["source_md5"] == hashlib.md5(NEW).hexdigest()
        async with RawServer({"/o": raw_response(ontology_zip(NEW, timestamp=(2025, 1, 1, 0, 0, 0)))}) as server:
            row = redis.entry("flopo")
            row["source_url"] = server.url("/o")
            await redis.hset("registered_servers", "flopo", __import__("json").dumps(row))
            again = await updater.execute_update_pipeline("flopo", row, redis, es, str(tmp_path), "")
        assert again["status"] == "unchanged", again


async def test_large_zip_payload_is_unpacked_with_bounded_memory(tmp_path):
    # Keep input construction outside tracing: only the download/unpack is measured.
    size = 32 << 20
    body = ontology_zip(payload=b"x" * size)
    tracemalloc.start()
    try:
        result, dest, _ = await download(tmp_path, _Resp(body))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["size"] == dest.stat().st_size == size
    assert peak < 16 << 20, f"ZIP unpacking allocated {peak} bytes for {size} output bytes"


async def test_zip_comment_may_contain_an_end_record_signature(tmp_path):
    stream = io.BytesIO(ontology_zip())
    with zipfile.ZipFile(stream, "a") as archive:
        archive.comment = b"release note PK\x05\x06 plus remaining comment text"
    result, dest, _ = await download(tmp_path, _Resp(stream.getvalue()))
    assert "error" not in result and dest.read_bytes() == OWL
