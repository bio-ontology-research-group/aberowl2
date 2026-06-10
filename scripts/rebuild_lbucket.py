#!/usr/bin/env python3
"""
Rebuild L-bucket worker(s) IN PLACE on onto, correctly:

- The config is rebuilt from the REGISTRY (the ontologies currently routed to
  worker-N) — NOT the stale on-disk worker_N_config.json, which may still list
  ontologies that have since been migrated to new workers (re-loading those
  would create duplicates).
- The reasoner for each ontology is taken from the stale config if present
  (e.g. fma=structural), else elk.
- Sizing is robust: launch GENEROUS (scaled by class count), let it FULLY load,
  measure live heap, then recreate trimmed at 1.4x live. (Measuring the old
  worker first is unreliable if it's crash-looping / under-sized.)
- Host port = 9200+N (old 90NN ports conflict). No repoint (registry url
  aberowl-worker-N:8080 is unchanged).

    sudo -n python3 rebuild_lbucket.py 2 3 9 ...
"""
import json, math, os, re, subprocess, sys, time

ONT = "/data/aberowl/ontologies"

def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()

def registry():
    raw = sh("docker", "exec", "deploy-redis-1", "redis-cli", "HVALS", "registered_servers")
    keep, classes, reasoner = {}, {}, {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = re.search(r"worker-(\d+):", d.get("url") or "")
        if not m:
            continue
        n = int(m.group(1))
        keep.setdefault(n, []).append(d.get("ontology"))
        classes[d.get("ontology")] = d.get("class_count") or 0
    return keep, classes

def stale_reasoner(n):
    try:
        return {o["id"]: o.get("reasoner", "elk") for o in json.load(open(f"{ONT}/worker_{n}_config.json"))}
    except Exception:
        return {}

def launch(n, xmx, mem, port, secret):
    sh("docker", "rm", "-f", f"aberowl-worker-{n}")
    return subprocess.run([
        "docker", "run", "-d", "--name", f"aberowl-worker-{n}", "--network", "aberowl-net",
        "-e", f"CONTAINER_ID=worker-{n}", "-e", f"ABEROWL_SECRET_KEY={secret}",
        "-e", "CENTRAL_ES_URL=http://deploy-elasticsearch-1:9200",
        "-e", "ELASTICSEARCH_URL=http://deploy-elasticsearch-1:9200",
        "-e", f"ONTOLOGY_PATH=/data/worker_{n}_config.json",
        "-e", f"JAVA_OPTS=-Xmx{xmx}g -Xms2g",
        "-v", f"{ONT}:/data:ro", "-v", "/data/aberowl/aberowlapi:/app/aberowlapi:ro",
        "-v", "/data/aberowl/docker/scripts:/scripts:ro",
        "-p", f"{port}:8080", f"--memory={mem}g", "--restart", "unless-stopped",
        "--log-opt", "max-size=30m", "--log-opt", "max-file=3",
        "aberowl-api", "python3", "/app/api_server.py", f"/data/worker_{n}_config.json",
    ], capture_output=True, text=True)

def wait_serving(port, want):
    for _ in range(45):
        time.sleep(12)
        c = sh("bash", "-lc",
               f'curl -s --max-time 8 "http://localhost:{port}/api/listLoadedOntologies.groovy" '
               f'| python3 -c "import json,sys;print(len(json.load(sys.stdin)[\\"ontologies\\"]))" 2>/dev/null')
        if c.isdigit() and int(c) >= want:
            return int(c)
    return int(c) if c.isdigit() else 0

def live_gb(n):
    pid = sh("docker", "exec", f"aberowl-worker-{n}", "bash", "-lc",
             "pgrep -f OntologyServer.groovy | head -1")
    if not pid:
        return None
    sh("docker", "exec", f"aberowl-worker-{n}", "jcmd", pid, "GC.run")
    time.sleep(3)
    out = sh("docker", "exec", f"aberowl-worker-{n}", "jcmd", pid, "GC.heap_info")
    m = re.search(r"used (\d+)K", out)
    return int(m.group(1)) / 1048576 if m else None

def main():
    secret = sh("bash", "-lc",
                "grep -E '^ABEROWL_SECRET_KEY=' /data/aberowl/deploy/.env | head -1 | cut -d= -f2-")
    keep, classes = registry()
    for n in [int(x) for x in sys.argv[1:]]:
        ids = sorted(keep.get(n, []))
        if not ids:
            print(f"worker-{n}: nothing routed here in registry — skip", flush=True); continue
        rmap = stale_reasoner(n)
        cfg = [{"id": i, "path": f"/data/{i}/{i}.owl", "reasoner": rmap.get(i, "elk")} for i in ids]
        open(f"{ONT}/worker_{n}_config.json", "w").write(json.dumps(cfg, indent=2))
        tot_cls = sum(classes.get(i, 0) for i in ids)
        tot_mb = sum((os.path.getsize(f"{ONT}/{i}/{i}.owl") if os.path.exists(f"{ONT}/{i}/{i}.owl") else 0)
                     for i in ids) / 1e6
        port = 9200 + n
        # Generous launch sized by class count AND file size (some big ontologies
        # have class_count=0 in the registry, e.g. mesh/loinc/cco — file size
        # catches those so we don't under-size and fail the load).
        gxmx = min(96, max(12, math.ceil(tot_cls / 4000) + 8, math.ceil(tot_mb / 12) + 8))
        print(f"worker-{n} {ids} ({tot_cls} cls): generous -Xmx{gxmx}g", flush=True)
        if launch(n, gxmx, gxmx + 6, port, secret).returncode != 0:
            print(f"  generous launch FAILED — skip", flush=True); continue
        served = wait_serving(port, len(ids))
        live = live_gb(n)
        if live is None:
            print(f"  served {served}/{len(ids)} but could not measure — leaving at generous {gxmx+6}G", flush=True); continue
        xmx = max(2, math.ceil(1.4 * live)); mem = xmx + (4 if xmx >= 12 else 2)
        print(f"  loaded {served}/{len(ids)}, live={live:.1f}G -> trim to {mem}G/-Xmx{xmx}g", flush=True)
        launch(n, xmx, mem, port, secret)
        served2 = wait_serving(port, len(ids))
        oom = sh("bash", "-lc", f"docker logs aberowl-worker-{n} --since 6m 2>&1 | grep -c OutOfMemoryError")
        rss = sh("bash", "-lc", f'docker stats --no-stream --format "{{{{.MemUsage}}}}" aberowl-worker-{n}')
        print(f"  -> DONE served {served2}/{len(ids)}, {mem}G/-Xmx{xmx}g, RSS {rss}, OOM {oom}", flush=True)

if __name__ == "__main__":
    main()
