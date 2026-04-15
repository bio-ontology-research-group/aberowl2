#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Launch AberOWL worker containers based on worker_plan.json.

Reads a worker plan produced by plan_workers.py, then for each worker
runs `docker run` with the memory limit and port allocated by the
plan. Idempotent: if a container of the same name is already running,
it is left alone unless --recreate is passed.

Usage (on onto):
    uv run deploy/launch_workers.py \\
        --plan /data/aberowl/ontologies/worker_plan.json \\
        --env /data/aberowl/deploy/.env \\
        --port-start 9015

    # Dry-run: print commands but don't execute
    uv run deploy/launch_workers.py --plan ... --dry-run

    # Recreate even if running
    uv run deploy/launch_workers.py --plan ... --recreate

    # Launch a single worker
    uv run deploy/launch_workers.py --plan ... --only 15
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PORTS_IN_USE_ON_ONTO = {8080, 8085, 8086}  # reserved by other services


def load_env(env_file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def container_exists(name: str) -> bool:
    r = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return name in r.stdout.strip().splitlines()


def container_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return name in r.stdout.strip().splitlines()


def build_docker_cmd(worker: dict, env: dict, port: int) -> list[str]:
    n = worker["number"]
    ram_gb = worker["ram_gb"]
    # Leave ~2GB headroom for JVM overhead & page cache
    xmx = max(2, ram_gb - 2)
    name = f"aberowl-worker-{n}"
    cfg_path_in_container = f"/data/worker_{n}_config.json"

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--network", "aberowl-net",
        "-e", f"CONTAINER_ID=worker-{n}",
        "-e", f"ABEROWL_SECRET_KEY={env['ABEROWL_SECRET_KEY']}",
        "-e", "CENTRAL_VIRTUOSO_URL=http://deploy-virtuoso-1:8890",
        "-e", "CENTRAL_ES_URL=http://deploy-elasticsearch-1:9200",
        "-e", "ELASTICSEARCH_URL=http://deploy-elasticsearch-1:9200",
        "-e", f"ONTOLOGY_PATH={cfg_path_in_container}",
        "-e", f"JAVA_OPTS=-Xmx{xmx}g -Xms2g",
        "-v", "/data/aberowl/ontologies:/data:ro",
        "-v", "/data/aberowl/aberowlapi:/app/aberowlapi:ro",
        "-v", "/data/aberowl/docker/scripts:/scripts:ro",
        "-p", f"{port}:8080",
        f"--memory={ram_gb}g",
        "--restart", "unless-stopped",
        "--log-opt", "max-size=30m",
        "--log-opt", "max-file=3",
        "aberowl-api",
        "python3", "/app/api_server.py", cfg_path_in_container,
    ]
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, type=Path)
    ap.add_argument("--env", required=True, type=Path)
    ap.add_argument("--port-start", type=int, default=9015)
    ap.add_argument("--only", type=int, help="launch only this worker number")
    ap.add_argument("--recreate", action="store_true",
                    help="stop+remove existing container before launching")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text())
    env = load_env(args.env)

    if "ABEROWL_SECRET_KEY" not in env:
        print("ERROR: ABEROWL_SECRET_KEY not in env file", file=sys.stderr)
        sys.exit(1)

    workers = plan["workers"]
    if args.only is not None:
        workers = [w for w in workers if w["number"] == args.only]
        if not workers:
            print(f"No worker numbered {args.only} in plan", file=sys.stderr)
            sys.exit(1)

    first_n = plan["workers"][0]["number"]
    launched, skipped, recreated = [], [], []

    for w in workers:
        n = w["number"]
        port = args.port_start + (n - first_n)
        if port in PORTS_IN_USE_ON_ONTO:
            print(f"SKIP worker-{n}: port {port} reserved")
            continue
        name = f"aberowl-worker-{n}"

        if container_exists(name):
            if args.recreate:
                print(f"Recreating {name}")
                if not args.dry_run:
                    subprocess.run(["docker", "rm", "-f", name], check=False,
                                   capture_output=True)
                recreated.append(n)
            else:
                print(f"SKIP {name}: already exists (use --recreate to replace)")
                skipped.append(n)
                continue

        cmd = build_docker_cmd(w, env, port)
        short = f"worker-{n} port={port} ram={w['ram_gb']}g bucket={w['bucket']} " \
                f"ont={w['ontology_count']}"
        if args.dry_run:
            print(f"DRY-RUN {short}")
            print("  " + " ".join(cmd))
        else:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                print(f"OK      {short}")
                launched.append(n)
            else:
                print(f"FAIL    {short}: {r.stderr.strip()[:200]}")

    print()
    print(f"Launched: {len(launched)}  Skipped (exists): {len(skipped)}  Recreated: {len(recreated)}")


if __name__ == "__main__":
    main()
