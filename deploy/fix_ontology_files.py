#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Detect and fix ontology files that aren't plain OWL.

BioPortal occasionally serves ontology downloads in compressed or
archived form, keeping the `.owl` extension. This script walks the
ontologies directory, classifies each file, and repairs what it can:

  * gzip  -> in-place gunzip
  * zip   -> extract, pick largest *.owl/*.rdf/*.ttl, replace

Files that are already valid (XML/text) are left alone. Anything
unrecognised is reported but not touched.

Usage:
    uv run deploy/fix_ontology_files.py /data/aberowl/ontologies
    uv run deploy/fix_ontology_files.py /data/aberowl/ontologies --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def magic(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read(8)


def is_gzip(path: Path) -> bool:
    return magic(path)[:2] == b"\x1f\x8b"


def is_zip(path: Path) -> bool:
    return magic(path)[:4] == b"PK\x03\x04" or magic(path)[:4] == b"PK\x05\x06"


def fix_gzip(path: Path, dry_run: bool) -> tuple[bool, str]:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        if dry_run:
            return True, "would gunzip"
        with gzip.open(path, "rb") as gz, tmp.open("wb") as out:
            shutil.copyfileobj(gz, out, length=1024 * 1024)
        size_out = tmp.stat().st_size
        tmp.replace(path)
        return True, f"gunzipped ({size_out:,} bytes)"
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return False, f"gunzip failed: {e}"


def fix_zip(path: Path, dry_run: bool) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            if not members:
                return False, "empty zip"
            # Prefer .owl, then .rdf, then .ttl, then largest
            def score(info):
                n = info.filename.lower()
                return (
                    4 if n.endswith(".owl") else
                    3 if n.endswith(".rdf") else
                    2 if n.endswith(".ttl") else
                    1 if n.endswith((".obo", ".xml", ".json")) else 0,
                    info.file_size,
                )
            chosen = max(members, key=score)
            if dry_run:
                return True, f"would extract {chosen.filename} ({chosen.file_size:,}B) from {len(members)} members"
            with tempfile.NamedTemporaryFile(
                dir=path.parent, delete=False, prefix=".tmp_", suffix=".owl"
            ) as tmp_fh:
                tmp_path = Path(tmp_fh.name)
                with zf.open(chosen) as src:
                    shutil.copyfileobj(src, tmp_fh, length=1024 * 1024)
            size_out = tmp_path.stat().st_size
            tmp_path.replace(path)
            return True, f"extracted {chosen.filename} ({size_out:,} bytes)"
    except Exception as e:
        return False, f"zip extract failed: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results = {"gzip_fixed": [], "zip_fixed": [], "failed": [], "other": []}

    for path in sorted(args.root.glob("*/*.owl")):
        try:
            header = magic(path)
        except Exception as e:
            results["failed"].append((str(path), f"read error: {e}"))
            continue

        if header[:2] == b"\x1f\x8b":
            ok, msg = fix_gzip(path, args.dry_run)
            (results["gzip_fixed"] if ok else results["failed"]).append((str(path), msg))
        elif header[:4] == b"PK\x03\x04":
            ok, msg = fix_zip(path, args.dry_run)
            (results["zip_fixed"] if ok else results["failed"]).append((str(path), msg))
        else:
            # Leave alone — XML, text, OBO etc. should be handled by OWLAPI
            pass

    for cat in ("gzip_fixed", "zip_fixed"):
        print(f"--- {cat} ({len(results[cat])}) ---")
        for p, msg in results[cat]:
            print(f"  {p}: {msg}")
    if results["failed"]:
        print(f"--- failed ({len(results['failed'])}) ---")
        for p, msg in results["failed"]:
            print(f"  {p}: {msg}")

    print(f"\nTotal repaired: {len(results['gzip_fixed']) + len(results['zip_fixed'])}  "
          f"Failed: {len(results['failed'])}")


if __name__ == "__main__":
    main()
