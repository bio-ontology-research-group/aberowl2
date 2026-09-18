#!/usr/bin/env python3
"""Heuristically scan only the selected release files, without printing contents.

Exit 0 means no findings, not proof that every credential was detected. Exit 1
means findings or files that could not safely be inspected. This checks current
selected bytes only, not Git history, unlisted files, archives or remote systems.
Potential credential files and symlinks are rejected before reading. Findings
contain only relative path, line number (0 for file-level findings), and category.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SECRET_NAME = re.compile(
    r"(?:^|_)(?:api_key|apikey|secret(?:_key)?|password|passwd|"
    r"access_token|auth_token|refresh_token|token|client_secret|"
    r"access_key(?:_id)?|private_key)(?:$|_)", re.I
)
PROVIDER_TOKEN = re.compile(
    r"\b(?:sk-(?:or-v1-|ant-api\d+-)?[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"hf_[A-Za-z0-9]{25,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"AKIA[A-Z0-9]{16})\b"
)
PRIVATE_PEM = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
URL_SECRET = re.compile(
    r"[?&](api[_-]?key|access[_-]?token|auth[_-]?token|token|"
    r"password|passwd|secret|client[_-]?secret)=([^\s&\"'<>`]+)", re.I
)
PLACEHOLDER = re.compile(
    r"(?:your[_ -].*|insert[_ -].*|replace[_ -].*|example(?:[_ -].*)?|"
    r"placeholder(?:[_ -].*)?|changeme|change-me|change_me|"
    r"(?:dummy|test|testing|dev|development|fake)(?:[_ -].*)?|"
    r"not-a-real(?:[_ -].*)?|redacted|none|null|n/?a)", re.I
)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    category: str


def is_secret_name(name: str) -> bool:
    # Normalize camelCase alongside the usual environment-variable naming.
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).replace("-", "_")
    return bool(SECRET_NAME.search(normalized)) and not re.search(
        r"(?:limit|length|count|budget|timeout|expires|expiry|enabled|ttl|header|env_var|env_name)",
        normalized, re.I,
    )


def is_placeholder(value: str) -> bool:
    value = value.strip()
    return (not value or value in {"-", "...", "***"}
            or bool(PLACEHOLDER.fullmatch(value))
            or any(marker in value for marker in ("${", "{{", "{api", "{token", "{key"))
            or (value.startswith("<") and value.endswith(">")))


def rejected_filename(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        name = part.lower()
        if (name in {".env", ".git"} or name.startswith(".env.")
                or name in {".authinfo", ".authinfo.gpg", ".netrc", "credentials",
                            "credentials.json", "credentials.yaml", "credentials.yml",
                            "secrets.json", "secrets.yaml", "secrets.yml", "id_rsa",
                            "id_ed25519", "id_ecdsa", "id_dsa"}
                or name.endswith((".pem", ".p12", ".pfx", ".key"))):
            return True
    return False


def literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_string(node.left), literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return literal_string(node.slice) or ""
    return ""


def scan_text(path: str, source: str) -> list[Finding]:
    findings = set()
    for number, line in enumerate(source.splitlines(), 1):
        if PRIVATE_PEM.search(line):
            findings.add(Finding(path, number, "private-key"))
        if PROVIDER_TOKEN.search(line):
            findings.add(Finding(path, number, "provider-token"))
        for match in URL_SECRET.finditer(line):
            if not is_placeholder(match.group(2)):
                findings.add(Finding(path, number, "credential-url-parameter"))
    if not path.endswith(".py"):
        return sorted(findings)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        findings.add(Finding(path, 0, "python-parse-error"))
        return sorted(findings)

    def check(name: str, node: ast.AST, category: str) -> None:
        value = literal_string(node)
        if is_secret_name(name) and value is not None and not is_placeholder(value):
            findings.add(Finding(path, node.lineno, category))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                check(target_name(target), node.value, "literal-credential-assignment")
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            check(target_name(node.target), node.value, "literal-credential-assignment")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    check(literal_string(key) or "", value, "literal-credential-assignment")
        elif isinstance(node, ast.Call):
            func = node.func
            is_getenv = ((isinstance(func, ast.Attribute) and func.attr == "getenv")
                         or (isinstance(func, ast.Name) and func.id == "getenv"))
            is_environ_get = (isinstance(func, ast.Attribute)
                              and func.attr in {"get", "setdefault"}
                              and isinstance(func.value, (ast.Attribute, ast.Name))
                              and target_name(func.value) == "environ")
            if (is_getenv or is_environ_get) and node.args:
                name = literal_string(node.args[0]) or ""
                if len(node.args) > 1:
                    check(name, node.args[1], "credential-environment-default")
                for keyword in node.keywords:
                    if keyword.arg == "default":
                        check(name, keyword.value, "credential-environment-default")
    return sorted(findings)


def selected_paths(manifest: dict) -> list[str]:
    groups = manifest["groups"]
    documentation = manifest["release_documentation"]
    if not isinstance(groups, dict) or not isinstance(documentation, list):
        raise ValueError("invalid manifest structure")
    paths = list(documentation)
    for values in groups.values():
        if not isinstance(values, list):
            raise ValueError("invalid manifest group")
        paths.extend(values)
    if not all(isinstance(path, str) for path in paths):
        raise ValueError("invalid manifest path")
    return sorted(set(paths))


def scan_release(root: Path, manifest: dict) -> list[Finding]:
    findings = []
    root = root.resolve()
    for relative in selected_paths(manifest):
        path_parts = PurePosixPath(relative)
        if path_parts.is_absolute() or ".." in path_parts.parts or "\\" in relative:
            findings.append(Finding(relative, 0, "unsafe-path"))
            continue
        if rejected_filename(relative):
            findings.append(Finding(relative, 0, "credential-filename-not-read"))
            continue
        path = root / relative
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
            findings.append(Finding(relative, 0, "symlink-not-read"))
            continue
        if not path.is_file():
            findings.append(Finding(relative, 0, "missing-or-nonregular-file"))
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            findings.append(Finding(relative, 0, "unreadable-file"))
            continue
        # Provider/private-key strings remain visible in binary encodings that
        # preserve ASCII. This is not an archive decoder or binary secret audit.
        source = raw.decode("utf-8", errors="replace")
        findings.extend(scan_text(relative, source))
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="release/contents.json")
    args = parser.parse_args()
    # Refuse unsafe/custom secret manifest names before opening them, too.
    manifest_path = PurePosixPath(args.manifest)
    if (manifest_path.is_absolute() or ".." in manifest_path.parts
            or rejected_filename(args.manifest)):
        print("release/contents.json:0:unsafe-manifest-path")
        return 1
    try:
        path = args.root / args.manifest
        root = args.root.resolve()
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
            raise ValueError("symlink manifest")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        findings = scan_release(args.root, manifest)
    except (OSError, ValueError, KeyError, TypeError):
        print("release/contents.json:0:invalid-or-unreadable-manifest")
        return 1
    for finding in findings:
        print(f"{finding.path}:{finding.line}:{finding.category}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
