"""
Helpers for the transfer service tests: the real service on a loopback port,
and file trees standing for the separate hosts.
"""

import hashlib
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict

import uvicorn

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "transfer_service"))

import aberowl_transfer  # noqa: E402

OLD = b"<rdf:RDF><!-- flopo release 1 --></rdf:RDF>\n"
NEW = b"<rdf:RDF><!-- flopo release 2 --></rdf:RDF>\n"
TOKEN = "transfer-test-token-" + "0123456789abcdef" * 2


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def real(path: Path) -> Path:
    """The service refuses directories reached through a symlink."""
    return Path(os.path.realpath(path))


def worker_host(root: Path, ontologies=("flopo", "po", "pato")) -> Path:
    """A worker host's ontology directory: what its workers mount at /data."""
    root.mkdir(parents=True, exist_ok=True)
    for oid in ontologies:
        (root / oid).mkdir()
        (root / oid / f"{oid}.owl").write_bytes(OLD)
    (root / "import_catalogues").mkdir()
    (root / "import_catalogues" / "flopo.json").write_text(
        '{"imports": {"http://purl.obolibrary.org/obo/po.owl": "/data/po/po.owl"}}')
    return root


def service_config(data_root: Path, state_dir: Path, ontologies=("flopo", "po"), **over):
    state_dir.mkdir(parents=True, exist_ok=True)
    raw = {"data_root": str(data_root), "state_dir": str(state_dir), "ontologies": list(ontologies),
           "min_free_bytes": 0, "lock_timeout_seconds": 0.5}
    raw.update(over)
    return aberowl_transfer.build_config(raw, TOKEN.encode())


def snapshot(root: Path) -> Dict[str, str]:
    """Every file and symlink under root, by relative path."""
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            files[rel] = "link:" + os.readlink(path) if path.is_symlink() else sha(path.read_bytes())
    return files


class ServiceThread:
    """The transfer service served by uvicorn on 127.0.0.1, in a thread."""

    def __init__(self, app, **uvicorn_options):
        """uvicorn_options: ssl_certfile and ssl_keyfile serve HTTPS."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        scheme = "https" if uvicorn_options.get("ssl_certfile") else "http"
        self.url = f"{scheme}://127.0.0.1:{self.port}"
        self.app = app
        config = uvicorn.Config(app, loop="asyncio", http="h11", lifespan="off",
                                log_level="warning", access_log=False, **uvicorn_options)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the transfer service did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()
        return False


def make_tls_files(directory: Path, openssl: str) -> Path:
    """A test CA, a server certificate it signed for 127.0.0.1, and an
    unrelated CA. Throwaway keys, valid for two days."""
    import subprocess

    def run(*args):
        subprocess.run([openssl, *args], cwd=directory, check=True, capture_output=True)

    for name in ("ca", "other-ca"):
        (directory / f"{name}.cnf").write_text(
            "[req]\ndistinguished_name = dn\nprompt = no\n"
            f"[dn]\nCN = AberOWL transfer test {name}\n"
            "[v3_ca]\nbasicConstraints = critical,CA:TRUE\nkeyUsage = critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid:always\n")
        run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-config", f"{name}.cnf",
            "-extensions", "v3_ca", "-keyout", f"{name}.key", "-out", f"{name}.pem")
    (directory / "server.ext").write_text(
        "basicConstraints = CA:FALSE\nkeyUsage = critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage = serverAuth\nsubjectAltName = IP:127.0.0.1\n"
        "subjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid,issuer\n")
    run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-config", "ca.cnf", "-subj", "/CN=127.0.0.1",
        "-keyout", "server.key", "-out", "server.csr")
    run("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-days", "2", "-extfile", "server.ext", "-out", "server.pem")
    return directory


def wait_until(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()
