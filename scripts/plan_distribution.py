#!/usr/bin/env python3
"""Plan a balanced ontology-to-worker distribution using class-count metrics.

Pulls the same data as fleet_report.py (aberowl + BioPortal fill-in), then
bin-packs ontologies into workers using a class-count-based memory model
calibrated against what we've observed empirically (biomodels at 187k
classes OOM'd at 16 GB; ncbitaxon at 2.7M needs 96 GB; etc.).

Outputs:
  - results/plan_distribution_<date>.json — machine-readable plan
    [{worker_n, memory_gb, ontologies: [{id, path, reasoner, classes}]}, ...]
  - results/plan_distribution_<date>.html — side-by-side current vs proposed

Does NOT apply changes. Apply manually via deploy/launch_workers.py or
by writing the worker_N_config.json files and restarting containers.

Usage:
    python3 scripts/plan_distribution.py
    python3 scripts/plan_distribution.py --preserve-memory
    python3 scripts/plan_distribution.py --no-current   # don't ssh for current configs
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import requests

# Reuse the data layer from fleet_report
sys.path.insert(0, str(Path(__file__).parent))
from fleet_report import (  # noqa: E402
    fetch_servers, gather, DEFAULT_CENTRAL, DEFAULT_BP_APIKEY, DEFAULT_CACHE,
)

DEFAULT_OUTPUT_HTML = Path(f"results/plan_distribution_{dt.date.today().isoformat()}.html")
DEFAULT_OUTPUT_JSON = Path(f"results/plan_distribution_{dt.date.today().isoformat()}.json")
DEFAULT_SSH_HOST = "onto"
N_WORKERS = 33

# Memory model calibrated to observations:
#   - biomodels 187k classes OOM'd at 16 GB → need at least 24 GB
#   - FMA 105k structural reasoner, 24 GB → fine
#   - ncbitaxon 2.7M, 96 GB → fine
#   - small ontologies (< 1k classes) → 1-2 GB each, but JVM overhead is ~4 GB minimum
def estimate_solo_memory_gb(classes: int | None, dl_expr: str | None) -> int:
    """Estimated heap need (in GB) for one ontology classified with ELK alone.

    Tightened to match observations: biomodels @ 187k needed 24G; CHEBI @ 224k
    fit in 16G (tight); MONDO @ 58k fit in 32G with headroom; ncbitaxon @ 2.7M
    fits in 96G. Strategy: start lean, bump on OOM during deployment.
    """
    n = classes or 1
    if n < 1_000:
        base = 4
    elif n < 10_000:
        base = 6
    elif n < 50_000:
        base = 8
    elif n < 150_000:
        base = 12
    elif n < 300_000:
        base = 20
    elif n < 500_000:
        base = 24
    elif n < 1_000_000:
        base = 32
    elif n < 3_000_000:
        base = 64
    else:
        base = 96
    # Non-EL profiles can blow ELK up — modest bump
    if dl_expr and not all(c in "EL+" for c in dl_expr):
        base = math.ceil(base * 1.3)
    return base


# Shared JVM/process overhead (GB). One worker = one JVM; ontologies on the
# same worker share this floor.
JVM_BASE_GB = 4

# Marginal heap (GB) each *additional* co-located ontology adds, per 1000 of
# its classes. The dominant ontology drives the reasoning peak (its full solo
# estimate); every other ontology's marginal cost is ~proportional to its size,
# NOT its padded solo estimate (a small ontology's solo is mostly the shared
# JVM floor, so summing solo-minus-floor across many tiny ontologies invents
# tens of GB that the live fleet never uses). Fit against uncapped multi-
# ontology workers — incl. worker-10 (29 ontologies / 45k classes in 7.9 GB)
# and worker-14 (24 ontologies in 6.5 GB): 0.10 covers every observed worker
# without under-provisioning, while keeping many-small workers near their JVM
# floor.
MARGINAL_GB_PER_1K_CLASSES = 0.10


def estimate_worker_memory_gb(ontologies: list[dict]) -> int:
    """Heap need for a worker hosting one or many ontologies in one JVM.

    mem = solo(dominant) + marginal-per-class * (classes of all other
    ontologies). The dominant (largest) ontology sets the reasoning peak; the
    rest add a size-proportional marginal cost. Never drops below the dominant
    ontology's solo estimate. Replaces the old model that sized from the
    dominant ontology only and under-provisioned co-located large reasoners.
    """
    if not ontologies:
        return JVM_BASE_GB
    solos = {id(o): estimate_solo_memory_gb(o.get("classes"), o.get("dl_expressivity"))
             for o in ontologies}
    dominant = max(ontologies, key=lambda o: solos[id(o)])
    mem = solos[id(dominant)]
    for o in ontologies:
        if o is dominant:
            continue
        # Marginal cost scales with size, but never exceeds what this ontology
        # would need standalone above the shared JVM floor (a second large
        # reasoner can't cost more co-located than it would alone).
        marginal = min(MARGINAL_GB_PER_1K_CLASSES * (o.get("classes") or 0) / 1000,
                       solos[id(o)] - JVM_BASE_GB)
        mem += max(0.0, marginal)
    return max(int(math.ceil(mem)), solos[id(dominant)])


# Bucket assignment rules: (name, min_classes, max_classes, max_per_worker, class_budget)
BUCKETS = [
    ("xl",   500_000,  float("inf"),  1,        float("inf")),
    ("l",    100_000,  500_000,       2,        800_000),
    ("m",     10_000,  100_000,       10,       300_000),
    ("s",      1_000,   10_000,       40,       100_000),
    ("xs",         0,    1_000,      150,        50_000),
]


def class_bucket(n: int | None) -> str:
    n = n or 0
    for name, lo, hi, _, _ in BUCKETS:
        if lo <= n < hi:
            return name
    return "xs"


@dataclass
class WorkerSlot:
    n: int
    bucket: str = ""
    ontologies: list[dict] = field(default_factory=list)
    memory_gb: int = 12

    @property
    def total_classes(self) -> int:
        return sum((o.get("classes") or 0) for o in self.ontologies)

    @property
    def total_individuals(self) -> int:
        return sum((o.get("individuals") or 0) for o in self.ontologies)


def fetch_current_configs(ssh_host: str) -> dict[int, list[dict]]:
    """SSH to onto and read every worker_N_config.json. Empty dict on failure.

    Returns plain shell-tarred bundle: prints each file with a marker line
    we can parse client-side. Avoids inline-python-over-ssh quoting hell.
    """
    out: dict[int, list[dict]] = {}
    try:
        # Emit each config file prefixed by "==== worker_N_config.json ===="
        cmd = (
            "for f in /data/aberowl/ontologies/worker_*_config.json; do "
            "  [ -f \"$f\" ] || continue; "
            "  echo \"==== $(basename $f) ====\"; "
            "  sudo -n cat \"$f\"; "
            "  echo; "
            "done"
        )
        r = subprocess.run(
            ["ssh", ssh_host, cmd],
            capture_output=True, text=True, timeout=60, check=True,
        )
        # Parse blocks
        current_name: str | None = None
        current_lines: list[str] = []
        def flush() -> None:
            if current_name is None:
                return
            try:
                n = int(current_name.split("_")[1])
            except (IndexError, ValueError):
                return
            body = "\n".join(current_lines).strip()
            if not body:
                out[n] = []
                return
            try:
                out[n] = json.loads(body)
            except json.JSONDecodeError:
                out[n] = []
        for line in r.stdout.splitlines():
            if line.startswith("==== ") and line.endswith(" ===="):
                flush()
                current_name = line.strip("= ").strip()
                current_lines = []
            else:
                current_lines.append(line)
        flush()
    except Exception as e:
        print(f"WARN: couldn't read current configs from {ssh_host}: {e}", file=sys.stderr)
    return out


def fetch_on_disk_ontologies(ssh_host: str, min_size: int = 20_000) -> set[str]:
    """List ontology IDs that have an OWL file on disk on onto (>min_size bytes).

    Looks at /data/aberowl/ontologies/<id>/<id>.owl. Returns set of ids.
    """
    out: set[str] = set()
    try:
        r = subprocess.run(
            ["ssh", ssh_host,
             f"sudo -n find /data/aberowl/ontologies -mindepth 2 -maxdepth 2 "
             f"-name '*.owl' -type f -size +{min_size}c -printf '%f\\n'"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.endswith(".owl"):
                out.add(line[:-4].lower())
    except Exception as e:
        print(f"WARN: couldn't list on-disk ontologies from {ssh_host}: {e}", file=sys.stderr)
    return out


def fetch_current_memory_limits(ssh_host: str) -> dict[int, int]:
    """SSH to onto and read each worker's docker memory limit (in GB)."""
    out: dict[int, int] = {}
    try:
        r = subprocess.run(
            ["ssh", ssh_host,
             "for n in $(seq 1 33); do "
             "  m=$(sudo -n docker inspect aberowl-worker-$n --format '{{.HostConfig.Memory}}' 2>/dev/null); "
             "  if [ -n \"$m\" ]; then echo \"$n $((m/1073741824))\"; fi; "
             "done"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[int(parts[0])] = int(parts[1])
    except Exception as e:
        print(f"WARN: couldn't read memory limits from {ssh_host}: {e}", file=sys.stderr)
    return out


# Ontologies whose true memory footprint is far larger than their class count
# implies: ABox/individual-heavy datasets that are huge FILES with very few
# classes (ISO-15926 reference data, authority/registry lists). Class-count
# bucketing places them in small (xs/s) workers where they OOM — rdl alone needs
# ~10 GB. They are pinned to a dedicated fixed-size worker instead.
# Measured 2026-06-08: these 6 = 30 GB live combined -> a 48 GB / -Xmx40g worker
# (deployed as physical aberowl-worker-37). See worker_measurements ledger.
PINNED_GIANTS = {
    "rdl", "lcgft", "ror", "fast-title", "xref-funder-reg", "nlmvs",
}
PINNED_GIANTS_MEMORY_GB = 48


def plan(records: list[dict], preserve_memory: bool, current_mem: dict[int, int]) -> list[WorkerSlot]:
    """Greedy bin-pack ontologies into workers respecting bucket rules.

    Iterates over buckets xl → xs, assigning ontologies (largest first within
    each bucket) to workers. xl gets a dedicated worker. Others fill workers
    up to the bucket's max_per_worker and class_budget. PINNED_GIANTS are pulled
    out first onto a dedicated, fixed-size worker.
    """
    # Pin ABox-heavy giants onto a dedicated worker; class-count bucketing would
    # mis-size them into small workers where they OOM.
    pinned = [r for r in records if (r.get("id") or "").lower() in PINNED_GIANTS]
    records = [r for r in records if (r.get("id") or "").lower() not in PINNED_GIANTS]

    # Group records by bucket, drop entries with no classes data
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    skipped = []
    for r in records:
        if not r.get("classes"):
            skipped.append(r["id"])
            continue
        by_bucket[class_bucket(r["classes"])].append(r)

    # Sort each bucket largest first so big ones get placed earliest
    for b in by_bucket.values():
        b.sort(key=lambda o: -(o.get("classes") or 0))

    slots: list[WorkerSlot] = []
    next_worker_n = 1

    def new_slot(bucket_name: str, memory_gb: int) -> WorkerSlot:
        nonlocal next_worker_n
        s = WorkerSlot(n=next_worker_n, bucket=bucket_name, memory_gb=memory_gb)
        next_worker_n += 1
        slots.append(s)
        return s

    def finalize_mem(s: WorkerSlot) -> None:
        # Size memory from the full ontology set once packing is done — each
        # reasoner needs its own working set on top of the shared JVM floor.
        mem = estimate_worker_memory_gb(s.ontologies)
        if preserve_memory:
            mem = min(mem, current_mem.get(s.n, mem))
        s.memory_gb = mem

    # Plan xl first — each gets dedicated worker
    for r in by_bucket.get("xl", []):
        s = new_slot("xl", JVM_BASE_GB)
        s.ontologies.append(r)
        finalize_mem(s)

    # Plan l, m, s, xs greedily
    for bname, lo, hi, max_per, class_budget in BUCKETS:
        if bname == "xl":
            continue
        bucket_list = by_bucket.get(bname, [])
        i = 0
        while i < len(bucket_list):
            s = new_slot(bname, JVM_BASE_GB)
            while i < len(bucket_list) and len(s.ontologies) < max_per:
                cand = bucket_list[i]
                if s.ontologies and s.total_classes + (cand.get("classes") or 0) > class_budget:
                    break
                s.ontologies.append(cand)
                i += 1
            finalize_mem(s)

    # Dedicated worker for the pinned giants: fixed measured size, not the
    # class-count model (which under-sizes ABox-heavy data).
    if pinned:
        g = new_slot("giants", PINNED_GIANTS_MEMORY_GB)
        g.ontologies.extend(pinned)

    return slots


def render_html(slots: list[WorkerSlot], current_cfg: dict[int, list[dict]],
                current_mem: dict[int, int], base_url: str, total_servers: int) -> str:
    today = dt.date.today().isoformat()

    def fnum(n):
        return f"{n:,}" if (n is not None and n != 0) else "—"

    # Build "current" view keyed by worker_n
    current_view = []
    for n in sorted(current_cfg.keys()):
        ids = [c["id"] for c in current_cfg[n]]
        current_view.append({
            "n": n,
            "mem_gb": current_mem.get(n, "?"),
            "ids": ids,
            "count": len(ids),
        })

    # Proposed view
    proposed_view = []
    for s in slots:
        proposed_view.append({
            "n": s.n,
            "mem_gb": s.memory_gb,
            "bucket": s.bucket,
            "classes": s.total_classes,
            "ids": [o["id"] for o in s.ontologies],
            "count": len(s.ontologies),
        })

    # Per-worker diff: pair current[i] with proposed[i] for visual comparison
    pair_count = max(len(current_view), len(proposed_view))

    current_rows = []
    for i in range(pair_count):
        if i < len(current_view):
            v = current_view[i]
            id_list = ", ".join(v["ids"][:5]) + ("..." if len(v["ids"]) > 5 else "")
            current_rows.append(
                f"<tr><td>worker-{v['n']}</td><td>{v['mem_gb']}G</td>"
                f"<td>{v['count']}</td><td class=ids>{html.escape(id_list)}</td></tr>"
            )
        else:
            current_rows.append("<tr><td>—</td><td>—</td><td>—</td><td>—</td></tr>")

    proposed_rows = []
    for i in range(pair_count):
        if i < len(proposed_view):
            v = proposed_view[i]
            id_list = ", ".join(v["ids"][:5]) + ("..." if len(v["ids"]) > 5 else "")
            cur_mem = current_mem.get(v["n"])
            mem_change = ""
            if cur_mem and cur_mem != v["mem_gb"]:
                arrow = "↑" if v["mem_gb"] > cur_mem else "↓"
                mem_change = f" <span class=memchange>{arrow}{v['mem_gb']-cur_mem:+}G</span>"
            proposed_rows.append(
                f"<tr><td>worker-{v['n']} <span class=bucket_{v['bucket']}>{v['bucket']}</span></td>"
                f"<td>{v['mem_gb']}G{mem_change}</td>"
                f"<td>{v['count']}</td>"
                f"<td>{fnum(v['classes'])}</td>"
                f"<td class=ids>{html.escape(id_list)}</td></tr>"
            )
        else:
            proposed_rows.append("<tr><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>")

    # Memory delta summary
    cur_total = sum(current_mem.values())
    proposed_total = sum(s.memory_gb for s in slots)

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>distribution plan {today}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 20px; color: #222; }}
  h1, h2 {{ color: #111; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  table {{ border-collapse: collapse; font-size: 12px; width: 100%; }}
  th, td {{ padding: 3px 6px; border-bottom: 1px solid #ddd; text-align: left; vertical-align: top; }}
  th {{ background: #f4f4f4; }}
  td.ids {{ max-width: 380px; word-break: break-word; color: #555; }}
  .bucket_xl {{ background: #fdd; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
  .bucket_l  {{ background: #fed; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
  .bucket_m  {{ background: #ffd; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
  .bucket_s  {{ background: #dfd; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
  .bucket_xs {{ background: #ddf; padding: 1px 4px; border-radius: 3px; font-size: 11px; }}
  .memchange {{ color: #c33; font-weight: bold; }}
  .note {{ background: #fffae6; border-left: 3px solid #f7b733; padding: 8px; margin: 10px 0; }}
</style>
</head><body>
<h1>aberowl2 distribution plan</h1>
<p><b>Source data:</b> {html.escape(base_url)} &middot;
   <b>Generated:</b> {dt.datetime.now().isoformat(timespec='seconds')} &middot;
   <b>Total ontologies:</b> {total_servers}</p>
<p><b>Workers in plan:</b> {len(slots)} (max {N_WORKERS}) &middot;
   <b>Memory budget current vs proposed:</b> {cur_total}G → {proposed_total}G
   ({proposed_total - cur_total:+}G)</p>

<div class=note>
This is a <b>proposal</b>, not applied. To apply: write each worker_N_config.json,
adjust memory limits where indicated (docker rm + docker run), then restart each
worker one at a time.
</div>

<div class=grid>
  <div>
    <h2>Current distribution</h2>
    <table>
    <thead><tr><th>worker</th><th>memory</th><th>count</th><th>ontologies</th></tr></thead>
    <tbody>{''.join(current_rows)}</tbody>
    </table>
  </div>
  <div>
    <h2>Proposed distribution</h2>
    <table>
    <thead><tr><th>worker</th><th>memory</th><th>count</th><th>classes</th><th>ontologies</th></tr></thead>
    <tbody>{''.join(proposed_rows)}</tbody>
    </table>
  </div>
</div>

</body></html>"""


def match_plan_to_current(slots: list[WorkerSlot], current_cfg: dict[int, list[dict]]) -> dict[int, int | None]:
    """Greedy match plan worker → existing worker by ontology-ID overlap.

    Walks plan slots in size order (largest first), and for each finds the
    unmatched current worker whose ontology set has the most IDs in common.
    Returns {plan_worker_n: matched_existing_worker_n or None}.
    """
    current_sets = {n: set(c["id"] for c in cfg) for n, cfg in current_cfg.items()}
    available = set(current_sets.keys())
    mapping: dict[int, int | None] = {}

    # Sort plan slots largest first
    sorted_slots = sorted(slots, key=lambda s: -s.total_classes)
    for s in sorted_slots:
        plan_ids = {o["id"] for o in s.ontologies}
        best_n, best_overlap = None, -1
        for cur_n in available:
            ovl = len(plan_ids & current_sets[cur_n])
            if ovl > best_overlap:
                best_n, best_overlap = cur_n, ovl
        # Only claim a current worker if there's at least one ontology in common
        if best_overlap > 0 and best_n is not None:
            mapping[s.n] = best_n
            available.discard(best_n)
        else:
            mapping[s.n] = None  # plan worker is "new"
    return mapping


def compute_diff(slots: list[WorkerSlot], current_cfg: dict[int, list[dict]],
                 current_mem: dict[int, int]) -> dict:
    """Compute per-worker diff between plan and current."""
    mapping = match_plan_to_current(slots, current_cfg)
    matched_current = {cur_n for cur_n in mapping.values() if cur_n is not None}

    matched, new, memory_changed, unchanged = [], [], [], []
    for s in slots:
        cur_n = mapping[s.n]
        plan_ids = {o["id"] for o in s.ontologies}
        if cur_n is None:
            new.append({
                "plan_n": s.n,
                "memory_gb": s.memory_gb,
                "bucket": s.bucket,
                "add": sorted(plan_ids),
            })
            continue
        cur_ids = {c["id"] for c in current_cfg.get(cur_n, [])}
        add = sorted(plan_ids - cur_ids)
        remove = sorted(cur_ids - plan_ids)
        cur_mem = current_mem.get(cur_n)
        mem_changed = cur_mem is not None and cur_mem != s.memory_gb
        rec = {
            "plan_n": s.n, "current_n": cur_n,
            "current_mem_gb": cur_mem, "planned_mem_gb": s.memory_gb,
            "bucket": s.bucket,
            "add": add, "remove": remove,
            "memory_changed": mem_changed,
        }
        if not add and not remove and not mem_changed:
            unchanged.append(rec)
        else:
            (memory_changed if mem_changed and not add and not remove else matched).append(rec)

    retired = [
        {"current_n": cur_n,
         "current_mem_gb": current_mem.get(cur_n),
         "remove": sorted(c["id"] for c in cfg)}
        for cur_n, cfg in current_cfg.items()
        if cur_n not in matched_current
    ]
    return {
        "unchanged": unchanged,
        "matched": matched,
        "memory_only": memory_changed,
        "new_workers": new,
        "retired_workers": retired,
    }


def render_diff_html(diff: dict, base_url: str) -> str:
    """Render the diff as a dedicated HTML page showing only what changes."""
    today = dt.date.today().isoformat()

    def chips(ids: list[str], cls: str) -> str:
        return " ".join(f'<span class="chip {cls}">{html.escape(i)}</span>' for i in ids)

    def mem_arrow(cur: int | None, plan: int) -> str:
        if cur is None or cur == plan:
            return f"<span class=mem>{plan}G</span>"
        delta = plan - cur
        cls = "memup" if delta > 0 else "memdown"
        return (f"<span class=mem>{cur}G &rarr; {plan}G "
                f"<span class='{cls}'>({delta:+}G)</span></span>")

    # Modified workers
    mod_rows = []
    for r in sorted(diff["matched"], key=lambda x: x["current_n"]):
        mem_html = mem_arrow(r["current_mem_gb"], r["planned_mem_gb"])
        mod_rows.append(f"""
        <tr>
          <td><b>worker-{r['current_n']}</b> <span class=bucket_{r['bucket']}>{r['bucket']}</span></td>
          <td>{mem_html}</td>
          <td class=count>+{len(r['add'])}</td><td>{chips(r['add'], 'add')}</td>
          <td class=count>-{len(r['remove'])}</td><td>{chips(r['remove'], 'rem')}</td>
        </tr>""")

    # Memory-only changes
    mem_rows = []
    for r in sorted(diff["memory_only"], key=lambda x: x["current_n"]):
        mem_rows.append(
            f"<tr><td><b>worker-{r['current_n']}</b></td>"
            f"<td>{mem_arrow(r['current_mem_gb'], r['planned_mem_gb'])}</td></tr>"
        )

    # New workers
    new_rows = []
    for r in sorted(diff["new_workers"], key=lambda x: x["plan_n"]):
        new_rows.append(f"""
        <tr>
          <td><b>plan worker-{r['plan_n']}</b> <span class=bucket_{r['bucket']}>{r['bucket']}</span></td>
          <td><span class=mem>{r['memory_gb']}G</span></td>
          <td class=count>+{len(r['add'])}</td><td>{chips(r['add'], 'add')}</td>
        </tr>""")

    # Retired workers
    retired_rows = []
    for r in sorted(diff["retired_workers"], key=lambda x: x["current_n"]):
        retired_rows.append(f"""
        <tr>
          <td><b>worker-{r['current_n']}</b></td>
          <td><span class=mem>{r['current_mem_gb']}G</span></td>
          <td class=count>-{len(r['remove'])}</td><td>{chips(r['remove'], 'rem')}</td>
        </tr>""")

    summary = (
        f"<b>unchanged:</b> {len(diff['unchanged'])} &middot; "
        f"<b>modified:</b> {len(diff['matched'])} &middot; "
        f"<b>memory-only:</b> {len(diff['memory_only'])} &middot; "
        f"<b>new:</b> {len(diff['new_workers'])} &middot; "
        f"<b>retired:</b> {len(diff['retired_workers'])}"
    )

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>distribution plan diff {today}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 20px; color: #222; }}
  h1, h2 {{ color: #111; }}
  h2 {{ margin-top: 30px; border-bottom: 2px solid #eee; padding-bottom: 4px; }}
  table {{ border-collapse: collapse; font-size: 13px; width: 100%; margin-top: 8px; }}
  th, td {{ padding: 6px 8px; border-bottom: 1px solid #e8e8e8; text-align: left; vertical-align: top; }}
  th {{ background: #f4f4f4; }}
  td.count {{ text-align: right; font-variant-numeric: tabular-nums;
              white-space: nowrap; color: #555; }}
  .chip {{ display: inline-block; padding: 1px 6px; margin: 1px;
           border-radius: 3px; font-size: 11px;
           font-family: ui-monospace, monospace; }}
  .chip.add {{ background: #e0f5e0; color: #060; border: 1px solid #c2e0c2; }}
  .chip.rem {{ background: #ffe5e5; color: #800; border: 1px solid #f0c0c0; }}
  .mem {{ font-family: ui-monospace, monospace; font-size: 12px; white-space: nowrap; }}
  .memup {{ color: #c33; font-weight: bold; }}
  .memdown {{ color: #060; font-weight: bold; }}
  .bucket_xl {{ background: #fdd; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
  .bucket_l  {{ background: #fed; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
  .bucket_m  {{ background: #ffd; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
  .bucket_s  {{ background: #dfd; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
  .bucket_xs {{ background: #ddf; padding: 1px 5px; border-radius: 3px; font-size: 11px; }}
  .summary {{ background: #f8f8f8; padding: 10px; border-radius: 4px; margin: 10px 0; }}
  .empty {{ color: #999; font-style: italic; }}
</style>
</head><body>
<h1>distribution plan diff</h1>
<p><b>Source:</b> {html.escape(base_url)} &middot;
   <b>Generated:</b> {dt.datetime.now().isoformat(timespec='seconds')}</p>
<div class=summary>{summary}</div>

<h2>Modified workers ({len(diff['matched'])})</h2>
{('<table><thead><tr><th>worker</th><th>memory</th><th>add</th><th>added ontologies</th><th>remove</th><th>removed ontologies</th></tr></thead><tbody>' + ''.join(mod_rows) + '</tbody></table>') if mod_rows else '<p class=empty>None.</p>'}

<h2>Memory-only changes ({len(diff['memory_only'])})</h2>
{('<table><thead><tr><th>worker</th><th>memory change</th></tr></thead><tbody>' + ''.join(mem_rows) + '</tbody></table>') if mem_rows else '<p class=empty>None.</p>'}

<h2>New workers to launch ({len(diff['new_workers'])})</h2>
{('<table><thead><tr><th>plan worker</th><th>memory</th><th>ontologies</th><th></th></tr></thead><tbody>' + ''.join(new_rows) + '</tbody></table>') if new_rows else '<p class=empty>None.</p>'}

<h2>Workers to retire ({len(diff['retired_workers'])})</h2>
{('<table><thead><tr><th>worker</th><th>current memory</th><th>removed</th><th>previously hosted</th></tr></thead><tbody>' + ''.join(retired_rows) + '</tbody></table>') if retired_rows else '<p class=empty>None.</p>'}

</body></html>"""


def print_diff(diff: dict) -> None:
    print(f"=== Diff summary ===")
    print(f"  unchanged:       {len(diff['unchanged'])} workers")
    print(f"  modified:        {len(diff['matched'])} workers")
    print(f"  memory-only:     {len(diff['memory_only'])} workers")
    print(f"  new in plan:     {len(diff['new_workers'])} workers")
    print(f"  retired:         {len(diff['retired_workers'])} workers")

    if diff["matched"]:
        print("\n--- modified workers (add/remove ontologies) ---")
        for r in sorted(diff["matched"], key=lambda x: x["current_n"]):
            tag = f" [{r['bucket']}]"
            mem = ""
            if r["memory_changed"]:
                mem = f" mem={r['current_mem_gb']}G→{r['planned_mem_gb']}G"
            print(f"  worker-{r['current_n']}{tag}{mem}  +{len(r['add'])} -{len(r['remove'])}")
            if r["add"]:
                print(f"    + {', '.join(r['add'][:10])}{'...' if len(r['add'])>10 else ''}")
            if r["remove"]:
                print(f"    - {', '.join(r['remove'][:10])}{'...' if len(r['remove'])>10 else ''}")

    if diff["memory_only"]:
        print("\n--- memory-only changes ---")
        for r in sorted(diff["memory_only"], key=lambda x: x["current_n"]):
            print(f"  worker-{r['current_n']}  {r['current_mem_gb']}G → {r['planned_mem_gb']}G")

    if diff["new_workers"]:
        print(f"\n--- new workers to launch ({len(diff['new_workers'])}) ---")
        for r in diff["new_workers"]:
            print(f"  plan worker-{r['plan_n']} [{r['bucket']}]  mem={r['memory_gb']}G  "
                  f"adds {len(r['add'])} ontologies")
            print(f"    + {', '.join(r['add'][:10])}{'...' if len(r['add'])>10 else ''}")

    if diff["retired_workers"]:
        print(f"\n--- workers to retire ({len(diff['retired_workers'])}) ---")
        for r in diff["retired_workers"]:
            print(f"  worker-{r['current_n']} (was {r['current_mem_gb']}G, "
                  f"held {len(r['remove'])} ontologies)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_CENTRAL)
    ap.add_argument("--bioportal-apikey", default=os.environ.get("BIOPORTAL_APIKEY", DEFAULT_BP_APIKEY))
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--ssh-host", default=DEFAULT_SSH_HOST,
                    help="ssh alias to read current configs/memory from")
    ap.add_argument("--no-current", action="store_true",
                    help="don't ssh; plan from scratch without current-state diff")
    ap.add_argument("--preserve-memory", action="store_true",
                    help="cap each worker's plan to its current memory limit")
    ap.add_argument("--no-bioportal", action="store_true")
    ap.add_argument("--out-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    ap.add_argument("--out-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    ap.add_argument("--diff", action="store_true",
                    help="emit a CLI diff of plan vs current state (implies reading current)")
    ap.add_argument("--diff-json", type=Path,
                    help="also write diff to JSON at this path")
    ap.add_argument("--diff-html", type=Path,
                    help="also write diff to a focused HTML page (changes-only view)")
    ap.add_argument("--include-missing", action="store_true",
                    help="include ontologies that have no OWL file on disk (default: skip)")
    ap.add_argument("--missing-md", type=Path,
                    default=Path(f"results/missing_ontologies_{dt.date.today().isoformat()}.md"),
                    help="write a markdown list of skipped ontologies (off-disk) here")
    args = ap.parse_args()

    print(f"Fetching /api/servers from {args.url}...", flush=True)
    servers = fetch_servers(args.url)
    records = gather(servers, args.bioportal_apikey, args.cache, not args.no_bioportal)
    print(f"  {len(records)} ontologies, "
          f"{sum(1 for r in records if r['source']!='unknown')} with metadata", flush=True)

    current_cfg, current_mem, on_disk = {}, {}, set()
    if not args.no_current:
        print(f"Reading current configs + memory from {args.ssh_host}...", flush=True)
        current_cfg = fetch_current_configs(args.ssh_host)
        current_mem = fetch_current_memory_limits(args.ssh_host)
        print(f"  {len(current_cfg)} configs, {len(current_mem)} memory limits", flush=True)
        if not args.include_missing:
            print(f"Reading on-disk ontology list from {args.ssh_host}...", flush=True)
            on_disk = fetch_on_disk_ontologies(args.ssh_host)
            print(f"  {len(on_disk)} ontologies have an OWL file on disk", flush=True)
            # Fail loud: if the disk listing came back empty we cannot trust the
            # plan (every ontology would silently pass the filter and we'd plan
            # undeployable zombies). Require --include-missing to opt out.
            if not on_disk:
                print("ERROR: on-disk ontology list is empty (ssh/find failed?). "
                      "Refusing to plan without disk verification. "
                      "Pass --include-missing to override.", file=sys.stderr)
                return 1

    # Filter records to only on-disk ontologies, unless --include-missing
    if on_disk:
        skipped = [r for r in records if (r["id"] or "").lower() not in on_disk]
        records = [r for r in records if (r["id"] or "").lower() in on_disk]
        print(f"  skipping {len(skipped)} ontologies without OWL files on disk", flush=True)
        # Write a markdown summary of the skipped set
        args.missing_md.parent.mkdir(parents=True, exist_ok=True)
        skipped_sorted = sorted(skipped, key=lambda r: r["id"] or "")
        # Group by likely reason: license-gated set documented in deploy/README.md
        LICENSE_GATED = {"meddra", "snomedct", "icd10", "icnp", "icpc2p", "hero",
                         "mddb", "nddf", "ndfrt", "rcd", "who-art"}
        gated = [r for r in skipped_sorted if (r["id"] or "").lower() in LICENSE_GATED]
        other = [r for r in skipped_sorted if (r["id"] or "").lower() not in LICENSE_GATED]
        md_lines = [
            f"# Ontologies missing from disk (as of {dt.date.today().isoformat()})",
            "",
            f"Of {len(skipped) + len(records)} registered ontologies, {len(skipped)} have no",
            "usable OWL file on disk under `/data/aberowl/ontologies/<id>/<id>.owl`.",
            "These are excluded from the current distribution plan.",
            "",
            "## Likely causes",
            "",
            "Per the deploy README's bulk-onboarding section, BioPortal returns:",
            "- ~65 `404 no_latest_submission` (abandoned submissions, nothing to download)",
            "- ~11 `403 license_restricted` (requires manual approval at bioontology.org)",
            "- Rest: parse errors, network blips, content issues",
            "",
            f"## License-gated ({len(gated)})",
            "",
            "These require per-account approval at bioontology.org before download:",
            "",
        ]
        for r in gated:
            md_lines.append(f"- `{r['id']}` ({r.get('title','')})")
        md_lines += [
            "",
            f"## Other missing ({len(other)})",
            "",
            "Generally abandoned BP submissions, broken OBO purls, or parse failures.",
            "Re-attempt by running `deploy/download_bioportal.py` or per-ontology curl.",
            "",
        ]
        for r in other:
            md_lines.append(f"- `{r['id']}` ({r.get('title','')})")
        args.missing_md.write_text("\n".join(md_lines))
        print(f"  wrote {args.missing_md}", flush=True)

    slots = plan(records, args.preserve_memory, current_mem)
    print(f"Plan: {len(slots)} workers; "
          f"buckets: {dict((b, sum(1 for s in slots if s.bucket==b)) for b in ['xl','l','m','s','xs'])}",
          flush=True)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(
        [{"worker_n": s.n, "memory_gb": s.memory_gb, "bucket": s.bucket,
          "ontologies": [{"id": o["id"], "path": f"/data/{o['id']}/{o['id']}.owl",
                          "reasoner": "elk", "classes": o.get("classes")}
                         for o in s.ontologies]}
         for s in slots],
        indent=2,
    ))
    args.out_html.parent.mkdir(parents=True, exist_ok=True)
    args.out_html.write_text(render_html(slots, current_cfg, current_mem, args.url, len(servers)))

    print(f"Wrote {args.out_json}", flush=True)
    print(f"Wrote {args.out_html}", flush=True)

    if args.diff or args.diff_json or args.diff_html:
        if not current_cfg:
            print("WARN: --diff needs current configs (don't pass --no-current)", file=sys.stderr)
            return 1
        diff = compute_diff(slots, current_cfg, current_mem)
        if args.diff:
            print()
            print_diff(diff)
        if args.diff_json:
            args.diff_json.parent.mkdir(parents=True, exist_ok=True)
            args.diff_json.write_text(json.dumps(diff, indent=2))
            print(f"Wrote {args.diff_json}", flush=True)
        if args.diff_html:
            args.diff_html.parent.mkdir(parents=True, exist_ok=True)
            args.diff_html.write_text(render_diff_html(diff, args.url))
            print(f"Wrote {args.diff_html}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
