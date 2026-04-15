"""
pytest fixtures for AberOWL2 integration tests.

All fixtures that start Docker containers are session-scoped so containers are
started once and shared across all tests that need them.

Usage
-----
    # Run all tests (slow ones included):
    uv run --extra test pytest tests/ -v

    # Skip tests that need Docker:
    uv run --extra test pytest tests/ -v -m "not slow"

    # Skip BioPortal live-API tests:
    uv run --extra test pytest tests/ -v -m "not bioportal"

Environment overrides
---------------------
    ABEROWL_REPO_PATH   – absolute path to repository root
                          (default: parent of this file's directory)
    ABEROWL_TEST_PORT_PIZZA   – nginx port for pizza stack  (default: 8082)
    ABEROWL_TEST_PORT_GO      – nginx port for go stack     (default: 8080)
    ABEROWL_TEST_CENTRAL_PORT – port for central-server     (default: 8099)
    ABEROWL_TEST_ES_PORT      – host-side ES port           (default: 19200)
    ABEROWL_TEST_VIRTUOSO_PORT– host-side Virtuoso HTTP port(default: 18890)
"""

import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Generator

import pytest
import requests

# ---------------------------------------------------------------------------
# Paths and port configuration
# ---------------------------------------------------------------------------
REPO = Path(os.getenv("ABEROWL_REPO_PATH", Path(__file__).parent.parent)).resolve()
DATA_DIR = REPO / "data"

PORT_PIZZA   = int(os.getenv("ABEROWL_TEST_PORT_PIZZA",    "8082"))
PORT_GO      = int(os.getenv("ABEROWL_TEST_PORT_GO",       "8080"))
PORT_CENTRAL = int(os.getenv("ABEROWL_TEST_CENTRAL_PORT",  "8099"))
PORT_ES      = int(os.getenv("ABEROWL_TEST_ES_PORT",       "19200"))
PORT_VIRT    = int(os.getenv("ABEROWL_TEST_VIRTUOSO_PORT", "18890"))

# Shared ontologies volume on the host
ONT_HOST_PATH = Path(os.getenv("ABEROWL_TEST_ONT_PATH", "/tmp/aberowl_test_ontologies"))

# A single secret key used by all per-ontology stacks in the test suite
TEST_SECRET_KEY = secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_http(url: str, timeout: int = 300, interval: float = 5.0) -> bool:
    """Poll url until it returns 2xx or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)
    return False


def _compose_up(env_file: Path, compose_file: Path, project: str, detach: bool = True):
    cmd = [
        "docker", "compose",
        "--env-file", str(env_file),
        "-f", str(compose_file),
        "-p", project,
        "up", "--build",
    ]
    if detach:
        cmd.append("-d")
    subprocess.run(cmd, check=True, cwd=str(REPO))


def _compose_down(env_file: Path, compose_file: Path, project: str):
    subprocess.run(
        [
            "docker", "compose",
            "--env-file", str(env_file),
            "-f", str(compose_file),
            "-p", project,
            "down", "-v", "--remove-orphans",
        ],
        check=False,
        cwd=str(REPO),
    )


def _ensure_network():
    subprocess.run(
        ["docker", "network", "create", "aberowl-net"],
        check=False,
        capture_output=True,
    )


def _write_ont_env(
    env_file: Path,
    *,
    ontology_id: str,
    nginx_port: int,
    es_host_port: int,
    virtuoso_host_port: int,
    secret_key: str,
):
    """Write a new-format env file for a per-ontology docker-compose stack."""
    # ES and Virtuoso are on the HOST network from the container perspective;
    # we use the host-gateway alias so containers can reach host-exposed ports.
    env_file.write_text(
        f"COMPOSE_PROJECT_NAME=aberowl_{nginx_port}\n"
        f"NGINX_PORT={nginx_port}\n"
        f"ONTOLOGY_ID={ontology_id}\n"
        f"ONTOLOGIES_HOST_PATH={ONT_HOST_PATH}\n"
        f"CENTRAL_VIRTUOSO_URL=http://host.docker.internal:{virtuoso_host_port}\n"
        f"CENTRAL_ES_URL=http://host.docker.internal:{es_host_port}\n"
        f"ELASTICSEARCH_URL=http://host.docker.internal:{es_host_port}\n"
        f"ABEROWL_PUBLIC_URL=http://localhost:{nginx_port}\n"
        f"ABEROWL_REGISTER=false\n"
        f"ABEROWL_CENTRAL_URL=\n"
        f"ABEROWL_SECRET_KEY={secret_key}\n"
    )


# ---------------------------------------------------------------------------
# Session-scoped: central infrastructure (ES + Virtuoso as standalone
# containers with host-accessible ports)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def central_es() -> Generator[str, None, None]:
    """
    Start a standalone Elasticsearch 7.17.10 container and expose it on
    PORT_ES on the host.  Yields the base URL ``http://localhost:{PORT_ES}``.
    """
    _ensure_network()
    container_name = "aberowl_test_es"
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "aberowl-net",
            "-p", f"{PORT_ES}:9200",
            "-e", "discovery.type=single-node",
            "-e", "ES_JAVA_OPTS=-Xms512m -Xmx512m",
            "elasticsearch:7.17.10",
        ],
        check=True,
    )
    url = f"http://localhost:{PORT_ES}"
    ready = _wait_http(f"{url}/_cluster/health?wait_for_status=yellow", timeout=120)
    assert ready, "Elasticsearch did not become healthy within 120 s"

    yield url

    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


@pytest.fixture(scope="session")
def central_virtuoso(tmp_path_factory) -> Generator[str, None, None]:
    """
    Start a Virtuoso container (built from the repo's Dockerfile.virtuoso)
    and expose SPARQL HTTP on PORT_VIRT.
    Yields the base URL ``http://localhost:{PORT_VIRT}``.
    """
    _ensure_network()
    container_name = "aberowl_test_virtuoso"
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # Build the Virtuoso image from the repo
    subprocess.run(
        ["docker", "build", "-f", "Dockerfile.virtuoso", "-t", "aberowl-test-virtuoso", "."],
        check=True,
        cwd=str(REPO),
    )
    subprocess.run(
        [
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "aberowl-net",
            "-p", f"{PORT_VIRT}:8890",
            "-e", "DBA_PASSWORD=dba",
            "-e", "SPARQL_UPDATE=true",
            "aberowl-test-virtuoso",
        ],
        check=True,
    )
    url = f"http://localhost:{PORT_VIRT}"
    ready = _wait_http(f"{url}/sparql", timeout=90)
    assert ready, "Virtuoso did not become reachable within 90 s"

    yield url

    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)


# ---------------------------------------------------------------------------
# Session-scoped: per-ontology stacks
# ---------------------------------------------------------------------------

def _make_ont_stack(ontology_id: str, owl_src: Path, nginx_port: int):
    """
    Shared logic for the pizza_stack and go_stack fixtures:
    - copies the OWL file into the shared volume directory
    - writes a new-format env file
    - runs docker compose up
    - waits for the Groovy API to respond
    - yields the base API URL
    - tears down the stack on exit
    """
    ont_dir = ONT_HOST_PATH / ontology_id
    ont_dir.mkdir(parents=True, exist_ok=True)
    dest = ont_dir / f"{ontology_id}_active.owl"
    if not dest.exists():
        shutil.copy2(owl_src, dest)

    env_dir = REPO / "env_files"
    env_dir.mkdir(exist_ok=True)
    env_file = env_dir / f"aberowl_{nginx_port}_test.env"
    project = f"aberowl_{nginx_port}"
    compose_file = REPO / "docker-compose.yml"

    _write_ont_env(
        env_file,
        ontology_id=ontology_id,
        nginx_port=nginx_port,
        es_host_port=PORT_ES,
        virtuoso_host_port=PORT_VIRT,
        secret_key=TEST_SECRET_KEY,
    )

    _compose_down(env_file, compose_file, project)   # clean slate
    _compose_up(env_file, compose_file, project)

    api_url = f"http://localhost:{nginx_port}/api"
    # Poll the health endpoint; GO classification can take several minutes
    timeout = 600 if ontology_id == "go" else 180
    ready = _wait_http(f"{api_url}/health.groovy", timeout=timeout)
    assert ready, f"{ontology_id} API did not become ready within {timeout} s"

    return api_url, env_file, compose_file, project


@pytest.fixture(scope="session")
def pizza_stack(central_es, central_virtuoso) -> Generator[str, None, None]:
    """
    Start the pizza ontology stack.  Yields the base API URL
    ``http://localhost:{PORT_PIZZA}/api``.

    Depends on central_es and central_virtuoso so infrastructure starts first.
    """
    api_url, env_file, compose_file, project = _make_ont_stack(
        "pizza", DATA_DIR / "pizza.owl", PORT_PIZZA
    )
    yield api_url
    _compose_down(env_file, compose_file, project)


@pytest.fixture(scope="session")
def go_stack(central_es, central_virtuoso) -> Generator[str, None, None]:
    """
    Start the GO ontology stack.  GO is 122 MB; ELK classification takes
    several minutes.  Timeout is set to 10 minutes.

    Yields the base API URL ``http://localhost:{PORT_GO}/api``.
    """
    go_owl = DATA_DIR / "go.owl"
    if not go_owl.exists():
        pytest.skip("data/go.owl not found (large file, download separately)")
    api_url, env_file, compose_file, project = _make_ont_stack(
        "go", go_owl, PORT_GO
    )
    yield api_url
    _compose_down(env_file, compose_file, project)


# ---------------------------------------------------------------------------
# Helpers for central server unit tests (no Docker needed)
# ---------------------------------------------------------------------------

@pytest.fixture
def central_server_path():
    """Return the path to the central_server directory."""
    return REPO / "central_server"
