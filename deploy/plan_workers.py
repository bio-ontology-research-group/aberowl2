#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Plan worker assignments for the full ontology set.

Reads existing worker_*_config.json files (to respect already-assigned
ontologies), plus a list of OWL files with sizes (either `find -printf
'%s %p'` output from onto, or read directly if the ontologies directory
is available), then bin-packs the unassigned ontologies into new
worker configs by size bucket.

Outputs worker_{start_n}_config.json..worker_N_config.json plus a
summary JSON (worker_plan.json) mapping each worker to its assignments,
size, and recommended memory limit.

Usage:
    # Produce a plan from a sizes file and existing configs:
    uv run deploy/plan_workers.py \\
        --sizes /tmp/ont_sizes.txt \\
        --existing /tmp/worker_configs \\
        --out /tmp/new_worker_configs \\
        --start 15
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


# Size buckets: (name, size_floor, size_ceiling, max_count, max_sum_bytes, ram_gb)
# Packing proceeds within each bucket greedily, largest first.
BUCKETS = [
    ("xl", 500_000_000, float("inf"), 1,   float("inf"), None),  # 1 per worker, RAM per ontology
    ("l",  100_000_000, 500_000_000,  3,   800_000_000, 16),
    ("m",   10_000_000, 100_000_000,  15,  400_000_000, 12),
    ("s",    1_000_000,  10_000_000,  80,  400_000_000, 12),
    ("xs",           0,   1_000_000,  300, 200_000_000, 12),
]


@dataclass
class WorkerPlan:
    number: int
    bucket: str
    ram_gb: int
    ontologies: list[tuple[str, int]] = field(default_factory=list)  # (id, size)

    @property
    def total_size(self) -> int:
        return sum(s for _, s in self.ontologies)

    def to_config(self) -> list[dict]:
        return [
            {"id": oid, "path": f"/data/{oid}/{oid}.owl", "reasoner": "elk"}
            for oid, _ in self.ontologies
        ]


def load_assigned(existing_dir: Path) -> set[str]:
    assigned: set[str] = set()
    for cfg_file in sorted(existing_dir.glob("worker_*_config.json")):
        try:
            data = json.loads(cfg_file.read_text())
            assigned.update(entry["id"] for entry in data)
        except Exception as e:
            print(f"WARN: failed to parse {cfg_file}: {e}")
    return assigned


def load_sizes(sizes_file: Path) -> list[tuple[str, int]]:
    """Parse `find -printf '%s %p'` output into [(ont_id, size_bytes), ...]."""
    out = []
    for line in sizes_file.read_text().splitlines():
        if not line.strip():
            continue
        size_s, path = line.split(None, 1)
        ont_id = path.strip().split("/")[-2]
        out.append((ont_id, int(size_s)))
    return out


def recommended_xl_ram(size_bytes: int) -> int:
    """Reasoner RAM for an XL ontology: max(24, ceil(size_gb * 6))."""
    size_gb = size_bytes / 1024**3
    return max(24, math.ceil(size_gb * 6))


def pack(unassigned: list[tuple[str, int]], start_worker: int) -> list[WorkerPlan]:
    plans: list[WorkerPlan] = []
    worker_n = start_worker

    for bucket_name, lo, hi, max_count, max_sum, ram_gb in BUCKETS:
        bucket = sorted(
            [o for o in unassigned if lo <= o[1] < hi], key=lambda x: -x[1]
        )
        if not bucket:
            continue

        current: WorkerPlan | None = None
        for ont_id, size in bucket:
            if bucket_name == "xl":
                plans.append(
                    WorkerPlan(
                        number=worker_n, bucket="xl",
                        ram_gb=recommended_xl_ram(size),
                        ontologies=[(ont_id, size)],
                    )
                )
                worker_n += 1
                continue

            if current is None or len(current.ontologies) >= max_count or \
                    current.total_size + size > max_sum:
                if current is not None:
                    plans.append(current)
                current = WorkerPlan(
                    number=worker_n, bucket=bucket_name, ram_gb=ram_gb
                )
                worker_n += 1
            current.ontologies.append((ont_id, size))

        if current is not None and current.ontologies:
            plans.append(current)

    return plans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", required=True, type=Path,
                    help="`find -printf '%s %p'` output for .owl files")
    ap.add_argument("--existing", required=True, type=Path,
                    help="Directory containing current worker_*_config.json")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output directory for new worker configs")
    ap.add_argument("--start", type=int, default=15,
                    help="First worker number to generate (default 15)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan but don't write files")
    args = ap.parse_args()

    assigned = load_assigned(args.existing)
    all_files = load_sizes(args.sizes)

    by_id: dict[str, int] = {}
    for oid, s in all_files:
        by_id[oid] = max(by_id.get(oid, 0), s)

    unassigned = [(oid, size) for oid, size in by_id.items() if oid not in assigned]

    print(f"On-disk ontologies:   {len(by_id)}")
    print(f"Already assigned:     {len(assigned)}  (across existing workers)")
    print(f"Unassigned to pack:   {len(unassigned)}")
    print()

    plans = pack(unassigned, args.start)

    total_ont = sum(len(p.ontologies) for p in plans)
    total_gb = sum(p.total_size for p in plans) / 1024**3
    print(f"Plan: {len(plans)} new workers, {total_ont} ontologies, {total_gb:.2f} GB")
    print()
    print(f"{'Worker':<10} {'Bucket':<6} {'Ont':>4} {'Size (GB)':>10} {'RAM (GB)':>9}  IDs")
    for p in plans:
        ids = ", ".join(oid for oid, _ in p.ontologies[:4])
        if len(p.ontologies) > 4:
            ids += f", ... (+{len(p.ontologies)-4})"
        print(f"worker-{p.number:<3} {p.bucket:<6} {len(p.ontologies):>4} "
              f"{p.total_size/1024**3:>10.3f} {p.ram_gb:>9}  {ids}")

    if args.dry_run:
        return

    args.out.mkdir(parents=True, exist_ok=True)
    summary = {"workers": []}
    for p in plans:
        path = args.out / f"worker_{p.number}_config.json"
        path.write_text(json.dumps(p.to_config(), indent=2))
        summary["workers"].append({
            "number": p.number,
            "bucket": p.bucket,
            "ram_gb": p.ram_gb,
            "ontology_count": len(p.ontologies),
            "total_size_gb": round(p.total_size / 1024**3, 3),
            "ontologies": [oid for oid, _ in p.ontologies],
        })
    (args.out / "worker_plan.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {len(plans)} configs + worker_plan.json to {args.out}")


if __name__ == "__main__":
    main()
