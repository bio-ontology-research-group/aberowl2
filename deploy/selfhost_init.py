#!/usr/bin/env python3
"""Self-hosting init helper for single-host AberOWL 2.

Two jobs, selected by subcommand, both driven off one ``ontologies/`` folder:

  prepare   For any web sources, download them into the folder, then write a
            canonical ``ontologies.json`` the worker loads. Runs BEFORE the
            worker starts.
  register  After the worker has loaded, register every loaded ontology with
            the central server and trigger its search index. Runs AFTER the
            worker is healthy.

Ontology input, simplest first:

  1. Bare files  — drop ``foo.owl`` into the folder. Id is derived from the
                   filename (``go.owl`` -> ``go``), reasoner defaults to ELK.
  2. Web sources — a ``sources.txt``: one entry per line, ``#`` comments allowed:
                     http://purl.obolibrary.org/obo/go.owl
                     hp   http://purl.obolibrary.org/obo/hp.owl
                     mp   http://purl.obolibrary.org/obo/mp.owl   hermit
                   (columns: [id] URL [reasoner]; id/reasoner optional.)
  3. Full control — an ``ontologies.config.json`` (advanced), authoritative if
                    present: ``[{"id","path"|"url","reasoner"}, ...]``.

Stdlib only, so it runs in a bare ``python:3.11-slim`` init container.
"""
import argparse
import base64
import gzip
import json
import os
import shutil
import ssl
import sys
import time
import urllib.request
import urllib.error

DEFAULT_REASONER = "elk"
GENERATED_CONFIG = "ontologies.json"        # what the worker loads (we write this)
USER_CONFIG = "ontologies.config.json"      # optional advanced input (authoritative)
SOURCES_FILE = "sources.txt"                # optional URL list
# Path the ontologies folder is mounted at inside the worker container.
WORKER_DATA_MOUNT = "/data"


# --------------------------------------------------------------------------
# Pure logic (unit-tested; no I/O)
# --------------------------------------------------------------------------

def derive_id(filename):
    """'GO.owl' / 'go_active.owl' -> 'go' (stem, lowercased, _active stripped)."""
    base = os.path.basename(filename)
    for ext in (".owl.gz", ".owl", ".rdf", ".ttl", ".obo"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    if base.endswith("_active"):
        base = base[: -len("_active")]
    return base.lower()


def is_url(s):
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def parse_sources(text):
    """Parse a sources.txt body into specs [{id, url, reasoner}].

    Each non-blank, non-comment line is: ``[id] URL [reasoner]``. The URL is the
    first http(s) token; an id before it and a reasoner after it are optional.
    """
    specs = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        toks = line.split()
        url_idx = next((i for i, t in enumerate(toks) if is_url(t)), None)
        if url_idx is None:
            raise ValueError(f"no http(s) URL in sources line: {raw!r}")
        url = toks[url_idx]
        ontology_id = toks[url_idx - 1] if url_idx >= 1 else derive_id(url)
        reasoner = toks[url_idx + 1] if url_idx + 1 < len(toks) else DEFAULT_REASONER
        specs.append({"id": ontology_id.lower(), "url": url, "reasoner": reasoner.lower()})
    return specs


def _normalize_spec(spec):
    """Fill defaults on a user-config entry; keep url/path as given."""
    out = {"reasoner": (spec.get("reasoner") or DEFAULT_REASONER).lower()}
    if spec.get("url"):
        out["url"] = spec["url"]
        out["id"] = (spec.get("id") or derive_id(spec["url"])).lower()
    elif spec.get("path"):
        out["path"] = spec["path"]
        out["id"] = (spec.get("id") or derive_id(spec["path"])).lower()
    else:
        raise ValueError(f"config entry needs 'url' or 'path': {spec!r}")
    return out


def resolve_specs(listing, user_config=None, sources_text=None):
    """Decide the ontology set from what's in the folder.

    `listing` is the list of filenames present in the folder. Precedence:
      - user_config (parsed ontologies.config.json) present -> authoritative;
      - else: every *.owl-ish file (bare-file mode) + sources.txt URLs.
    Returns specs with either 'path' (local, already present) or 'url' (fetch).
    """
    if user_config:
        return [_normalize_spec(s) for s in user_config]

    specs, seen = [], set()
    for name in sorted(listing):
        if name in (GENERATED_CONFIG, USER_CONFIG, SOURCES_FILE):
            continue
        if not name.lower().endswith((".owl", ".owl.gz", ".rdf", ".ttl", ".obo")):
            continue
        oid = derive_id(name)
        if oid in seen:
            continue
        seen.add(oid)
        specs.append({"id": oid, "path": name, "reasoner": DEFAULT_REASONER})

    for s in parse_sources(sources_text or ""):
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        specs.append(s)
    return specs


def onto_rel_path(ontology_id):
    """Layout every ontology at <id>/<id>.owl. Central hardcodes this convention
    for reindex (updater.execute_reindex: /data/{id}/{id}.owl), so the worker
    must serve it from the same place."""
    return f"{ontology_id}/{ontology_id}.owl"


def worker_config(specs, data_mount=WORKER_DATA_MOUNT):
    """Build the worker ontologies.json list: [{id, path, reasoner}], with each
    ontology at the mount's <id>/<id>.owl."""
    return [{
        "id": s["id"],
        "path": f"{data_mount.rstrip('/')}/{onto_rel_path(s['id'])}",
        "reasoner": s.get("reasoner", DEFAULT_REASONER),
    } for s in specs]


def extract_loaded_ids(list_loaded_response):
    """Pull ontology ids from a worker /listLoadedOntologies.groovy JSON body.

    Tolerates entries that are bare id strings or objects with id/ontology/name.
    """
    onts = list_loaded_response.get("ontologies", []) if isinstance(list_loaded_response, dict) else []
    ids = []
    for o in onts:
        if isinstance(o, str):
            ids.append(o)
        elif isinstance(o, dict):
            v = o.get("ontologyId") or o.get("id") or o.get("ontology") or o.get("name")
            if v:
                ids.append(v)
    return ids


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def _http(method, url, data=None, headers=None, timeout=60):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, r.read()


def _gunzip(src_gz, dest):
    with gzip.open(src_gz, "rb") as fi, open(dest, "wb") as fo:
        shutil.copyfileobj(fi, fo)


def download(url, dest_path, timeout=3600):
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "aberowl-selfhost"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r, open(dest_path, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    return dest_path


def wait_for(url, tries=60, delay=5.0):
    for _ in range(tries):
        try:
            code, _ = _http("GET", url, timeout=10)
            if 200 <= code < 500:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def wait_for_worker_ready(worker, tries=240, delay=5.0):
    """Poll the worker's health endpoint until at least one ontology is
    classified (status 'ok'). Classification of a large ontology can take
    minutes, so this waits generously. Returns the loaded-ids list or []."""
    url = f"{worker}/api/health.groovy"
    for _ in range(tries):
        try:
            code, body = _http("GET", url, timeout=10)
            if code == 200:
                data = json.loads(body)
                if data.get("status") == "ok" or data.get("totalClassified", 0) > 0:
                    return extract_loaded_ids(data)
        except Exception:
            pass
        time.sleep(delay)
    return []


def _basic_auth(user, password):
    tok = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def es_count(es_url, ontology_id):
    """Doc count of the ontology's ES class-index alias, or None if unreachable."""
    url = f"{es_url.rstrip('/')}/aberowl_{ontology_id}_classes/_count"
    try:
        code, body = _http("GET", url, timeout=10)
        if code == 200:
            return int(json.loads(body).get("count", 0))
    except Exception:
        return None
    return None


def wait_for_index(es_url, ontology_id, tries=48, delay=5.0):
    """Poll until the class index has documents (reindex is async on central)."""
    for _ in range(tries):
        c = es_count(es_url, ontology_id)
        if c:
            return c
        time.sleep(delay)
    return 0


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_prepare(args):
    folder = args.onto_dir
    listing = os.listdir(folder) if os.path.isdir(folder) else []

    user_config = None
    ucfg = os.path.join(folder, USER_CONFIG)
    if os.path.isfile(ucfg):
        with open(ucfg) as f:
            user_config = json.load(f)

    sources_text = ""
    src = os.path.join(folder, SOURCES_FILE)
    if os.path.isfile(src):
        with open(src) as f:
            sources_text = f.read()

    specs = resolve_specs(listing, user_config=user_config, sources_text=sources_text)
    if not specs:
        print("prepare: no ontologies found (no .owl files, sources.txt, or config).")
        return 1

    # Place every ontology at <folder>/<id>/<id>.owl so it matches the path
    # central asks the worker to index from.
    for s in specs:
        dest = os.path.join(folder, onto_rel_path(s["id"]))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if "url" in s:
            print(f"prepare: downloading {s['id']} <- {s['url']}")
            if s["url"].endswith(".gz"):
                tmp = dest + ".gz"
                download(s["url"], tmp)
                _gunzip(tmp, dest)
                os.remove(tmp)
            else:
                download(s["url"], dest)
        else:
            src = os.path.join(folder, os.path.basename(s["path"]))
            if os.path.abspath(src) != os.path.abspath(dest):
                shutil.copyfile(src, dest)

    cfg = worker_config(specs)
    out = os.path.join(folder, GENERATED_CONFIG)
    with open(out, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"prepare: wrote {out} with {len(cfg)} ontolog{'y' if len(cfg) == 1 else 'ies'}: "
          f"{', '.join(c['id'] for c in cfg)}")
    return 0


def cmd_register(args):
    central = args.central.rstrip("/")
    worker = args.worker_url.rstrip("/")

    if not wait_for(f"{central}/api/listOntologies"):
        print("register: central server never became reachable", file=sys.stderr)
        return 1
    print("register: waiting for the worker to load + classify its ontologies "
          "(this can take a while for large ones)...")
    ids = wait_for_worker_ready(worker)
    if not ids:
        print("register: worker never reported a classified ontology", file=sys.stderr)
        return 1
    print(f"register: worker loaded {len(ids)} ontolog{'y' if len(ids) == 1 else 'ies'}: {', '.join(ids)}")

    def trigger_reindex(oid):
        _http("POST", f"{central}/admin/ontology/{oid}/reindex",
              headers=_basic_auth(args.admin_user, args.admin_password), timeout=30)

    ok = 0
    for oid in ids:
        body = json.dumps({"ontology": oid, "url": worker}).encode()
        try:
            _http("POST", f"{central}/register", data=body,
                  headers={"Content-Type": "application/json"}, timeout=30)
        except urllib.error.HTTPError as e:
            print(f"  register {oid}: FAILED HTTP {e.code}", file=sys.stderr)
            continue
        try:
            trigger_reindex(oid)
        except urllib.error.HTTPError as e:
            print(f"  {oid}: registered, but reindex FAILED HTTP {e.code}", file=sys.stderr)
            continue

        # Reindex is async on central; wait for the ES index to populate so that
        # search works the moment `up` returns. Retry once if it stalls empty.
        if not args.es_url:
            print(f"  {oid}: registered + reindex triggered")
            ok += 1
            continue
        n = wait_for_index(args.es_url, oid, tries=24)
        if n == 0:
            print(f"  {oid}: index still empty, re-triggering reindex...")
            try:
                trigger_reindex(oid)
            except urllib.error.HTTPError:
                pass
            n = wait_for_index(args.es_url, oid, tries=24)
        if n == 0:
            print(f"  {oid}: registered, but the search index did not populate", file=sys.stderr)
            continue
        print(f"  {oid}: registered + indexed ({n} classes searchable)")
        ok += 1
    print(f"register: {ok}/{len(ids)} ontologies ready")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="download web sources + write ontologies.json")
    p.add_argument("--onto-dir", default="/data/ontologies")
    p.set_defaults(func=cmd_prepare)

    r = sub.add_parser("register", help="register loaded ontologies with central + index them")
    r.add_argument("--central", default="http://central-server:8000")
    r.add_argument("--worker-url", default="http://worker:8080")
    r.add_argument("--es-url", default=os.getenv("CENTRAL_ES_URL", "http://elasticsearch:9200"),
                   help="Elasticsearch URL to confirm the index populated ('' to skip the wait)")
    r.add_argument("--admin-user", default=os.getenv("ADMIN_USER", "admin"))
    r.add_argument("--admin-password", default=os.getenv("ADMIN_PASSWORD", "changeme"))
    r.set_defaults(func=cmd_register)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
