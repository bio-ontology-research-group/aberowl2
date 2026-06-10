#!/usr/bin/env python3
"""Fleet report: per-worker ontology distribution + sizing metrics + HTML output.

Pulls /api/servers from the central server (default: beta). For ontologies
without rich metadata in the registry, fills the gap by querying BioPortal's
/ontologies/{ACRONYM}/metrics endpoint (cached locally so re-runs are cheap).

Outputs a static HTML file with two sections:
  1. Per-worker table: online/total, totals across hosted ontologies
     (classes, individuals, properties), biggest ontology, and a size-bucket
     summary aligned with deploy/plan_workers.py's buckets.
  2. Per-ontology table (client-side sortable): id, worker, status, source
     of metrics (aberowl / bioportal / unknown), class/individual/property
     counts.

Usage:
    python3 scripts/fleet_report.py
    python3 scripts/fleet_report.py --no-bioportal      # skip the fill-in
    python3 scripts/fleet_report.py --output /tmp/r.html

Re-run safely; BioPortal calls are cached to /tmp/bp_metrics_cache.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import requests

DEFAULT_CENTRAL = "https://beta.aber-owl.net"
DEFAULT_BP_APIKEY = "8b5b7825-538d-40e0-9e9e-5ab9274a9aeb"  # same key as deploy/download_ontologies.py
DEFAULT_CACHE = Path("/tmp/bp_metrics_cache.json")
DEFAULT_OUTPUT = Path(f"results/fleet_report_{dt.date.today().isoformat()}.html")

# Size buckets aligned with deploy/plan_workers.py for class-count-based grouping.
# (name, min_classes, max_classes, suggested_per_worker)
CLASS_BUCKETS = [
    ("xl",  500_000,  float("inf"), 1),   # huge — dedicated worker
    ("l",   100_000,  500_000,      2),   # 1-3 per worker
    ("m",    10_000,  100_000,     10),
    ("s",     1_000,   10_000,     50),
    ("xs",        0,    1_000,    200),
]


def fetch_servers(base_url: str, timeout: int = 90) -> list[dict]:
    # The /api/servers payload is ~864 entries; beta's proxy chain occasionally
    # stalls — give it a generous timeout instead of erroring at 30s.
    r = requests.get(f"{base_url.rstrip('/')}/api/servers", timeout=timeout)
    r.raise_for_status()
    return r.json()


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True))


def fetch_bp_metrics(acronym: str, apikey: str, cache: dict, session: requests.Session) -> dict | None:
    """Return BP /metrics for an acronym, with on-disk cache. Returns None if unavailable."""
    key = acronym.upper()
    if key in cache:
        return cache[key]
    try:
        r = session.get(
            f"https://data.bioontology.org/ontologies/{key}/metrics",
            params={"apikey": apikey},
            timeout=15,
        )
        if r.status_code != 200:
            cache[key] = None
            return None
        data = r.json()
        # BP returns metrics; we only keep what we need.
        out = {
            "classes": data.get("classes"),
            "individuals": data.get("individuals"),
            "properties": data.get("properties"),
            "max_depth": data.get("maxDepth"),
            "avg_children": data.get("averageChildCount"),
        }
        cache[key] = out
        return out
    except Exception:
        cache[key] = None
        return None


def worker_num(url: str) -> int:
    try:
        return int(url.split("aberowl-worker-")[1].rstrip("/").rstrip(":8080"))
    except (IndexError, ValueError):
        return 9999


def class_bucket(n: int | None) -> str:
    if not n:
        return "?"
    for name, lo, hi, _ in CLASS_BUCKETS:
        if lo <= n < hi:
            return name
    return "?"


def gather(servers: list[dict], apikey: str, cache_path: Path, use_bp: bool) -> list[dict]:
    """Return a normalised list with metrics filled from aberowl or BP."""
    cache = load_cache(cache_path) if use_bp else {}
    session = requests.Session() if use_bp else None
    result = []
    for o in servers:
        rec = {
            "id": o.get("ontology"),
            "title": o.get("title"),
            "status": o.get("status"),
            "url": o.get("url"),
            "worker": worker_num(o.get("url", "")),
            "classes": o.get("class_count"),
            "individuals": o.get("individual_count"),
            "object_props": o.get("object_property_count"),
            "data_props": o.get("data_property_count"),
            "annotation_props": o.get("annotation_property_count"),
            "axioms": o.get("axiom_count"),
            "logical_axioms": o.get("logical_axiom_count"),
            "tbox_axioms": o.get("tbox_axiom_count"),
            "abox_axioms": o.get("abox_axiom_count"),
            "rbox_axioms": o.get("rbox_axiom_count"),
            "dl_expressivity": o.get("dl_expressivity"),
            "source": "aberowl" if o.get("class_count") else None,
        }
        if rec["classes"] is None and use_bp and rec["id"]:
            bp = fetch_bp_metrics(rec["id"], apikey, cache, session)
            if bp and bp.get("classes") is not None:
                rec["classes"] = bp["classes"]
                rec["individuals"] = bp.get("individuals")
                rec["object_props"] = bp.get("properties")
                rec["source"] = "bioportal"
        if rec["source"] is None:
            rec["source"] = "unknown"
        rec["bucket"] = class_bucket(rec["classes"])
        result.append(rec)
    if use_bp:
        save_cache(cache, cache_path)
    return result


def per_worker_summary(records: list[dict]) -> list[dict]:
    by_worker = defaultdict(list)
    for r in records:
        by_worker[r["worker"]].append(r)
    rows = []
    for w, onts in sorted(by_worker.items()):
        online = [o for o in onts if o.get("status") == "online"]
        bucket_counts = defaultdict(int)
        for o in onts:
            bucket_counts[o["bucket"]] += 1
        biggest = max(onts, key=lambda o: o.get("classes") or 0, default=None)
        rows.append({
            "worker": w,
            "total": len(onts),
            "online": len(online),
            "classes_sum": sum((o.get("classes") or 0) for o in onts),
            "axioms_sum": sum((o.get("logical_axioms") or 0) for o in onts),
            "individuals_sum": sum((o.get("individuals") or 0) for o in onts),
            "biggest": biggest["id"] if biggest else None,
            "biggest_classes": (biggest.get("classes") if biggest else None) or 0,
            "bucket_counts": dict(bucket_counts),
        })
    return rows


def render_html(records: list[dict], summary: list[dict], base_url: str) -> str:
    online = sum(1 for r in records if r["status"] == "online")
    total = len(records)
    no_meta = sum(1 for r in records if r["source"] == "unknown")
    bp_meta = sum(1 for r in records if r["source"] == "bioportal")

    # Helpers
    def fnum(n):
        return f"{n:,}" if (n is not None and n != 0) else "—"
    def cell(s):
        return html.escape(str(s) if s is not None else "—")

    bucket_legend = " · ".join(
        f"<b>{b[0]}</b>: ≥{b[1]:,} classes ({b[3]}/worker)" for b in CLASS_BUCKETS
    )

    # Per-worker rows
    worker_rows_html = []
    for s in summary:
        bcs = s["bucket_counts"]
        bucket_str = " ".join(
            f"<span class=bucket_{b}>{b}:{bcs.get(b, 0)}</span>"
            for b in ("xl", "l", "m", "s", "xs", "?") if bcs.get(b)
        )
        worker_rows_html.append(
            f"<tr><td>worker-{s['worker']}</td>"
            f"<td>{s['online']}/{s['total']}</td>"
            f"<td class=num>{fnum(s['classes_sum'])}</td>"
            f"<td class=num>{fnum(s['axioms_sum'])}</td>"
            f"<td class=num>{fnum(s['individuals_sum'])}</td>"
            f"<td>{cell(s['biggest'])} ({fnum(s['biggest_classes'])})</td>"
            f"<td>{bucket_str}</td></tr>"
        )

    # Per-ontology rows
    ont_rows_html = []
    for r in sorted(records, key=lambda x: (-(x.get("classes") or 0), x["id"] or "")):
        ont_rows_html.append(
            f"<tr class=row_{r['status']} bucket={r['bucket']}>"
            f"<td>{cell(r['id'])}</td>"
            f"<td>worker-{r['worker']}</td>"
            f"<td class=status_{r['status']}>{cell(r['status'])}</td>"
            f"<td class=src_{r['source']}>{cell(r['source'])}</td>"
            f"<td>{cell(r['bucket'])}</td>"
            f"<td class=num>{fnum(r['classes'])}</td>"
            f"<td class=num>{fnum(r['individuals'])}</td>"
            f"<td class=num>{fnum(r['object_props'])}</td>"
            f"<td class=num>{fnum(r['logical_axioms'])}</td>"
            f"<td>{cell(r['dl_expressivity'])}</td>"
            f"</tr>"
        )

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>aberowl2 fleet report {dt.date.today().isoformat()}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 20px; color: #222; }}
  h1, h2 {{ color: #111; }}
  table {{ border-collapse: collapse; margin: 10px 0; font-size: 13px; }}
  th, td {{ padding: 4px 8px; border-bottom: 1px solid #ddd; text-align: left; }}
  th {{ background: #f4f4f4; cursor: pointer; user-select: none; }}
  th:hover {{ background: #e8e8e8; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .status_online {{ color: #060; }}
  .status_offline {{ color: #c33; }}
  .src_aberowl {{ color: #444; }}
  .src_bioportal {{ color: #06a; }}
  .src_unknown {{ color: #999; font-style: italic; }}
  .bucket_xl {{ background: #fdd; padding: 1px 4px; border-radius: 3px; }}
  .bucket_l {{ background: #fed; padding: 1px 4px; border-radius: 3px; }}
  .bucket_m {{ background: #ffd; padding: 1px 4px; border-radius: 3px; }}
  .bucket_s {{ background: #dfd; padding: 1px 4px; border-radius: 3px; }}
  .bucket_xs {{ background: #ddf; padding: 1px 4px; border-radius: 3px; }}
  .legend {{ font-size: 12px; color: #555; margin: 5px 0; }}
  input[type=search] {{ padding: 4px; width: 220px; }}
</style>
</head><body>
<h1>aberowl2 fleet report</h1>
<p><b>Source:</b> {html.escape(base_url)} &middot;
   <b>Generated:</b> {dt.datetime.now().isoformat(timespec='seconds')} &middot;
   <b>Total:</b> {total} ontologies &middot;
   <b>Online:</b> {online} &middot;
   <b>Offline:</b> {total - online}</p>
<p class=legend>Metadata sources: aberowl ({sum(1 for r in records if r['source']=='aberowl')}),
   bioportal-fallback ({bp_meta}), unknown ({no_meta}). &middot;
   Class buckets: {bucket_legend}.</p>

<h2>Per-worker distribution</h2>
<table id=workers>
<thead><tr>
<th>worker</th><th>online/total</th><th>total classes</th><th>logical axioms</th>
<th>individuals</th><th>biggest</th><th>buckets</th>
</tr></thead>
<tbody>
{''.join(worker_rows_html)}
</tbody></table>

<h2>Per-ontology metrics</h2>
<p>Filter: <input type=search id=filter placeholder="ontology id or worker..."></p>
<table id=ontologies>
<thead><tr>
<th data-sort=str>id</th>
<th data-sort=str>worker</th>
<th data-sort=str>status</th>
<th data-sort=str>source</th>
<th data-sort=str>bucket</th>
<th data-sort=num>classes</th>
<th data-sort=num>individuals</th>
<th data-sort=num>object props</th>
<th data-sort=num>logical axioms</th>
<th data-sort=str>DL expressivity</th>
</tr></thead>
<tbody id=ontology_body>
{''.join(ont_rows_html)}
</tbody></table>

<script>
// minimal client-side sorting + filter
(function() {{
  const table = document.getElementById('ontologies');
  const tbody = document.getElementById('ontology_body');
  table.querySelectorAll('thead th').forEach((th, i) => {{
    th.addEventListener('click', () => {{
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const numCmp = th.dataset.sort === 'num';
      const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      th.dataset.dir = dir;
      rows.sort((a, b) => {{
        const av = a.children[i].textContent.replace(/,/g, '').trim();
        const bv = b.children[i].textContent.replace(/,/g, '').trim();
        if (numCmp) {{
          const an = parseFloat(av) || (av === '—' ? -1 : 0);
          const bn = parseFloat(bv) || (bv === '—' ? -1 : 0);
          return dir === 'asc' ? an - bn : bn - an;
        }} else {{
          return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
        }}
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
  document.getElementById('filter').addEventListener('input', e => {{
    const q = e.target.value.toLowerCase();
    tbody.querySelectorAll('tr').forEach(r => {{
      r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
    }});
  }});
}})();
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_CENTRAL,
                    help="central server base URL (default: %(default)s)")
    ap.add_argument("--bioportal-apikey", default=os.environ.get("BIOPORTAL_APIKEY", DEFAULT_BP_APIKEY),
                    help="BioPortal API key (default: deploy/download_ontologies.py key)")
    ap.add_argument("--no-bioportal", action="store_true",
                    help="don't fill missing metadata from BioPortal")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                    help="BioPortal response cache path")
    ap.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT,
                    help="HTML output path")
    args = ap.parse_args()

    print(f"Fetching /api/servers from {args.url}...", flush=True)
    servers = fetch_servers(args.url)
    print(f"  got {len(servers)} entries", flush=True)

    use_bp = not args.no_bioportal
    if use_bp:
        missing = sum(1 for o in servers if not o.get("class_count"))
        print(f"BioPortal fill-in for {missing} entries lacking metadata (cache={args.cache})...", flush=True)

    records = gather(servers, args.bioportal_apikey, args.cache, use_bp)

    sources = defaultdict(int)
    for r in records:
        sources[r["source"]] += 1
    print(f"  metadata sources: aberowl={sources['aberowl']}, "
          f"bioportal={sources['bioportal']}, unknown={sources['unknown']}", flush=True)

    summary = per_worker_summary(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(records, summary, args.url))
    print(f"Wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
