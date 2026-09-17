"""Offline regression tests: credentials stay out of URLs and diagnostics."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = 'synthetic-' + 'credential-for-tests'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {'BIOPORTAL_API_KEY': ''}):
        spec.loader.exec_module(module)
    return module


class CredentialTests(unittest.TestCase):
    def test_central_missing_key_and_safe_error(self):
        spec = importlib.util.spec_from_file_location('bioportal_under_test', ROOT / 'central_server/app/intake/bioportal.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'BIOPORTAL_API_KEY': ''}):
            spec.loader.exec_module(module)
        session = Mock()
        self.assertIsNone(asyncio.run(module._get_json(session, 'https://data.bioontology.org/ontologies')))
        session.get.assert_not_called()
        module.BIOPORTAL_API_KEY = SENTINEL
        session.get.side_effect = RuntimeError(SENTINEL)
        with self.assertLogs(module.logger, level='ERROR') as logs:
            self.assertIsNone(asyncio.run(module._get_json(session, 'https://data.bioontology.org/ontologies')))
        self.assertNotIn(SENTINEL, ' '.join(logs.output))
        self.assertNotIn(SENTINEL, session.get.call_args.args[0])
        self.assertEqual(session.get.call_args.kwargs['headers']['Authorization'], f'apikey token={SENTINEL}')

    def test_metadata_skips_without_key(self):
        m = load('fetch_metadata')
        with patch.object(m, 'urlopen') as request:
            self.assertIsNone(m.bp_fetch('GO'))
            self.assertIsNone(m.bp_fetch_latest_submission('GO'))
            request.assert_not_called()

    def test_metadata_header_not_url(self):
        m = load('fetch_metadata')
        m.BP_API_KEY = SENTINEL
        response = Mock()
        response.read.return_value = b'{}'
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        with patch.object(m, 'urlopen', return_value=context) as request:
            m.bp_fetch('GO')
            m.bp_fetch_latest_submission('GO')
        for call in request.call_args_list:
            req = call.args[0]
            self.assertNotIn(SENTINEL, req.full_url)
            self.assertEqual(req.get_header('Authorization'), f'apikey token={SENTINEL}')

    def test_catalog_requires_key(self):
        m = load('download_bioportal')
        with patch.object(m, 'urlopen') as request:
            with self.assertRaisesRegex(RuntimeError, 'BIOPORTAL_API_KEY'):
                m.fetch_catalog()
            request.assert_not_called()

    def test_bulk_download_redacts_failure(self):
        m = load('download_bioportal')
        m.API_KEY = SENTINEL
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', SENTINEL)) as run:
            result = m.download_one('GO', Path(tmp), 100)
            self.assertNotIn(SENTINEL, json.dumps(result))
            self.assertNotIn(SENTINEL, run.call_args.args[0][-1])
        with tempfile.TemporaryDirectory() as tmp, patch.object(m.subprocess, 'run', side_effect=RuntimeError(SENTINEL)):
            self.assertNotIn(SENTINEL, json.dumps(m.download_one('GO', Path(tmp), 100)))

    def test_direct_download_only_authenticates_bioportal(self):
        m = load('download_ontologies')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'BIOPORTAL_API_KEY': SENTINEL}), patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', SENTINEL)) as run:
            result = m.download_bioportal('GO', 'https://data.bioontology.org/ontologies/GO/download', Path(tmp))
            self.assertNotIn(SENTINEL, json.dumps(result))
            self.assertIn(f'Authorization: apikey token={SENTINEL}', run.call_args.args[0])
            m.download_bioportal('GO', 'https://purl.obolibrary.org/obo/go.owl', Path(tmp))
            self.assertNotIn(f'Authorization: apikey token={SENTINEL}', run.call_args.args[0])
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'BIOPORTAL_API_KEY': ''}), patch.object(m.subprocess, 'run') as run:
            result = m.download_bioportal('GO', 'https://data.bioontology.org/ontologies/GO/download', Path(tmp))
            self.assertEqual(result['status'], 'failed')
            run.assert_not_called()

    def test_retry_failure_redacted(self):
        m = load('retry_downloads')
        m.BP_API_KEY = SENTINEL
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, '_curl', return_value=subprocess.CompletedProcess([], 1, '', SENTINEL)):
            self.assertNotIn(SENTINEL, json.dumps(m.retry_one('go', Path(tmp), 100, 1)))
        m.BP_API_KEY = ''
        with tempfile.TemporaryDirectory() as tmp, patch.object(m, '_curl', return_value=subprocess.CompletedProcess([], 1, '', 'failed')) as curl:
            result = m.retry_one('go', Path(tmp), 100, 1)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(curl.call_count, 1)


if __name__ == '__main__':
    unittest.main()
