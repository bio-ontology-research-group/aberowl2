"""Synthetic tests; fixtures contain no real credentials."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCANNER = Path(__file__).resolve().parents[1] / 'release/check_credentials.py'
spec = importlib.util.spec_from_file_location('release_credential_scanner', SCANNER)
scanner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


class CredentialScannerTests(unittest.TestCase):
    def test_provider_and_private_key(self):
        token = 'sk-' + 'A' * 30
        pem = '-----BEGIN ' + 'RSA PRIVATE KEY-----'
        findings = scanner.scan_text('sample.txt', token + '\n' + pem)
        self.assertEqual([(x.line, x.category) for x in findings],
                         [(1, 'provider-token'), (2, 'private-key')])
        self.assertNotIn(token, repr(findings))

    def test_python_defaults_assignments_and_dictionary(self):
        source = '\n'.join([
            'import os',
            "BIOPORTAL_API_KEY = 'synthetic-value'",
            "x = os.getenv('ABEROWL_SECRET_KEY', 'synthetic-value')",
            "y = os.environ.get('ACCESS_TOKEN', default='synthetic-value')",
            "config = {'password': 'synthetic-value'}",
        ])
        findings = scanner.scan_text('sample.py', source)
        self.assertEqual([f.line for f in findings], [2, 3, 4, 5])
        self.assertNotIn('synthetic-value', repr(findings))

    def test_empty_external_configuration_and_dummy_defaults(self):
        source = '\n'.join([
            'import os',
            "API_KEY = os.environ['API_KEY']",
            "TOKEN = os.getenv('TOKEN', '')",
            "PASSWORD = 'development-only'",
            "SECRET_KEY = 'your-secret-key'",
            "TOKEN_RATE_LIMIT = '100'",
            "MAX_TOKENS = '8192'",
        ])
        self.assertEqual(scanner.scan_text('sample.py', source), [])

    def test_query_parameter_and_templates(self):
        source = '\n'.join([
            'https://example.invalid/a?' + 'apikey=synthetic-value',
            'https://example.invalid/a?token=${ACCESS_TOKEN}',
            'https://example.invalid/a?api_key=YOUR_API_KEY',
            'https://example.invalid/a?apikey={api_key}',
        ])
        self.assertEqual([f.line for f in scanner.scan_text('README.md', source)], [1])

    def test_forbidden_name_never_read(self):
        manifest = {'groups': {'files': ['.env', 'keys/id_rsa', '.git/config']}, 'release_documentation': []}
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'read_bytes') as read:
            findings = scanner.scan_release(Path(temp), manifest)
        read.assert_not_called()
        self.assertEqual(len(findings), 3)
        self.assertTrue(all(f.category == 'credential-filename-not-read' for f in findings))

    def test_only_selected_files_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'safe.txt').write_text('plain text')
            (root / 'unlisted.txt').write_text('sk-' + 'A' * 30)
            (root / 'link.txt').symlink_to(root / 'unlisted.txt')
            manifest = {'groups': {'files': ['safe.txt']}, 'release_documentation': []}
            self.assertEqual(scanner.scan_release(root, manifest), [])
            manifest['release_documentation'] = ['link.txt']
            self.assertEqual(scanner.scan_release(root, manifest)[0].category, 'symlink-not-read')

    def test_missing_path_traversal_and_parse_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = {'groups': {'files': ['../outside', 'missing.py']}, 'release_documentation': []}
            findings = scanner.scan_release(Path(temp), manifest)
        self.assertEqual({f.category for f in findings}, {'unsafe-path', 'missing-or-nonregular-file'})
        self.assertEqual(scanner.scan_text('bad.py', 'not valid python !')[0].category,
                         'python-parse-error')

    def test_cli_rejects_git_and_symlink_manifest_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'elsewhere').mkdir()
            (root / 'release').symlink_to(root / 'elsewhere', target_is_directory=True)
            for manifest in ['.git/config', 'release/contents.json']:
                result = subprocess.run(
                    [sys.executable, str(SCANNER), '--root', str(root), '--manifest', manifest],
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, '')
                self.assertTrue(result.stdout.startswith('release/contents.json:0:'))

    def test_cli_redacts_values_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'release').mkdir()
            (root / 'selected.py').write_text("API_KEY = 'synthetic-value'\n")
            (root / 'release/contents.json').write_text(json.dumps({
                'groups': {'files': ['selected.py']}, 'release_documentation': []}))
            result = subprocess.run([sys.executable, str(SCANNER), '--root', str(root)],
                                    text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, 'selected.py:1:literal-credential-assignment\n')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
