#!/usr/bin/env python3
"""
Normalize malformed Manchester-syntax OWL files that pack multiple
`Annotations:` keyword sections onto a single line, e.g.:

    Annotations: rsid "rs1208"    Annotations: rdfs:label "rs1208"    Annotations: relevant_for NAT2

OWLAPI 4.5.29's Manchester parser rejects this (older OWLAPI versions, like the
one the legacy aberowl runs, tolerated it). Splitting each inline `Annotations:`
onto its own line is semantically identical — repeated `Annotations:` sections
in a frame are additive — and parses cleanly.

The split is quote-aware: an `Annotations:` occurring inside a "double-quoted"
string literal is left untouched. Files are only rewritten if they actually
contain the malformation, and the original is backed up to <file>.orig.

Usage:
    # scan only (no writes) — report affected files
    python3 scripts/normalize_manchester_annotations.py /data/aberowl/ontologies --scan
    # fix specific files
    python3 scripts/normalize_manchester_annotations.py --apply FILE [FILE ...]
    # fix every affected file under a root
    python3 scripts/normalize_manchester_annotations.py /data/aberowl/ontologies --apply
"""

from __future__ import annotations

import argparse
import os
import sys


def annotation_keyword_positions(line: str) -> list[int]:
    """Offsets of 'Annotations:' keyword occurrences that are OUTSIDE quotes."""
    pos = []
    in_q = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_q = not in_q
            i += 1
        elif not in_q and line.startswith("Annotations:", i):
            pos.append(i)
            i += len("Annotations:")
        else:
            i += 1
    return pos


def split_line(line: str) -> list[str]:
    """Split a line carrying >1 inline 'Annotations:' into one per line."""
    body = line.rstrip("\n")
    positions = annotation_keyword_positions(body)
    if len(positions) <= 1:
        return [line]
    indent = body[: len(body) - len(body.lstrip())]
    out = []
    prefix = body[: positions[0]].rstrip()
    if prefix.strip():
        out.append(prefix)
    for k, p in enumerate(positions):
        end = positions[k + 1] if k + 1 < len(positions) else len(body)
        out.append(indent + body[p:end].rstrip())
    return [s + "\n" for s in out]


def file_is_affected(path: str) -> int:
    """Return count of lines with >1 inline Annotations: (0 = clean)."""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.count("Annotations:") > 1 and len(annotation_keyword_positions(line)) > 1:
                    n += 1
    except Exception:
        return 0
    return n


def normalize_file(path: str) -> int:
    """Rewrite the file with inline Annotations: split. Returns lines changed."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    out, changed = [], 0
    for line in lines:
        pieces = split_line(line)
        if len(pieces) > 1:
            changed += 1
        out.extend(pieces)
    if changed:
        if not os.path.exists(path + ".orig"):
            os.replace(path, path + ".orig")
        else:
            os.remove(path)
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(out)
    return changed


def iter_owl(root: str):
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(".owl"):
                yield os.path.join(dirpath, fn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="ontology root dir(s) and/or .owl files")
    ap.add_argument("--scan", action="store_true", help="report affected files, write nothing")
    ap.add_argument("--apply", action="store_true", help="rewrite affected files (backs up .orig)")
    args = ap.parse_args()
    if not args.scan and not args.apply:
        ap.error("pass --scan or --apply")

    targets: list[str] = []
    for p in args.paths:
        if os.path.isdir(p):
            targets.extend(iter_owl(p))
        else:
            targets.append(p)

    affected = []
    for path in targets:
        n = file_is_affected(path)
        if n:
            affected.append((path, n))

    print(f"Scanned {len(targets)} files; {len(affected)} affected.")
    for path, n in affected:
        print(f"  {n:4d} bad lines  {path}")

    if args.apply:
        print()
        for path, _ in affected:
            c = normalize_file(path)
            print(f"  fixed {c} lines in {path} (backup {path}.orig)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
