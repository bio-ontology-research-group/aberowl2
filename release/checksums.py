#!/usr/bin/env python3
"""Write or verify SHA256SUMS for the explicit release allowlist (stdlib only)."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def selected_paths(root):
    manifest = json.loads((root / 'release/contents.json').read_text())
    paths = [p for group in manifest['groups'].values() for p in group]
    paths += manifest['release_documentation']
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate release paths')
    for name in sorted(paths):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError('Unsafe release path')
        target = root / name
        if any((root.joinpath(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)):
            raise ValueError('Symlink in release selection')
        if name == 'release/SHA256SUMS':
            continue
        if not target.is_file():
            raise ValueError(f'Missing selected file: {name}')
        yield name, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = ''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}\n' for name, path in selected_paths(root))
    dest = root / 'release/SHA256SUMS'
    if args.check:
        if not dest.is_file() or dest.read_text() != inventory:
            raise SystemExit('Checksum inventory is absent or differs from selected files')
        print('All selected file checksums match')
    else:
        dest.write_text(inventory)
        print(f'Wrote {len(inventory.splitlines())} checksums (inventory excludes itself)')


if __name__ == '__main__':
    main()
