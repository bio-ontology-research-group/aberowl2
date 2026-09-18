#!/usr/bin/env python3
"""Export the checked release allowlist into a new directory (no Git history)."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from checksums import selected_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        parser.error('destination must not exist')
    subprocess.run([sys.executable, str(root / 'release/check_credentials.py')], check=True)
    subprocess.run([sys.executable, str(root / 'release/checksums.py'), '--check'], check=True)
    paths = list(selected_paths(root)) + [('release/SHA256SUMS', root / 'release/SHA256SUMS')]
    # Resolve all selected paths and finish checks before writing any output.
    destination.mkdir(parents=True)
    for relative, source in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    manifest = json.loads((root / 'release/contents.json').read_text())
    expected = {name for group in manifest['groups'].values() for name in group}
    expected.update(manifest['release_documentation'])
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    if actual != expected:
        raise SystemExit('Export file inventory differs from allowlist')
    subprocess.run([sys.executable, str(destination / 'release/checksums.py'), '--check'], check=True)
    subprocess.run([sys.executable, str(destination / 'release/check_credentials.py')], check=True)
    print(f'Exported and checked {len(actual)} files in {destination}')


if __name__ == '__main__':
    main()
