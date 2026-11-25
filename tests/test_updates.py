"""Updater checks using fixtures; never run a real installer."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
import update_core as core


class Response:
    def __init__(self, data=None, status=200, chunks=()):
        self.data = data
        self.status_code = status
        self.chunks = chunks
        self.url = 'https://release-assets.githubusercontent.com/file'
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
    def json(self): return self.data
    def iter_content(self, *args): return iter(self.chunks)


def release(tag='v1.1.0'):
    return {'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [{
        'name': core.ASSET_NAME, 'size': 3, 'digest': 'sha256:' + 'a'*64,
        'browser_download_url': f'https://github.com/{core.REPOSITORY}/releases/download/{tag}/{core.ASSET_NAME}',
    }]}


class UpdatesTests(unittest.TestCase):
    def test_versions_are_numeric(self):
        self.assertGreater(core.version('v1.10.0'), core.version('1.9.0'))
        for value in ('v1.1.0-beta', 'garbage', '1.1'):
            with self.assertRaises(ValueError): core.version(value)

    def test_the_asset_is_the_installer_from_this_repository(self):
        self.assertEqual(core.ASSET_NAME, 'Parallel-Setup.exe')
        self.assertEqual(core.REPOSITORY, 'Chukway/Parallel')

    def test_empty_repository_and_old_release(self):
        with patch.object(core.requests, 'get', return_value=Response(status=404)):
            self.assertIsNone(core.check_release('1.0.0'))
        with patch.object(core.requests, 'get', return_value=Response(release('v1.0.0'))):
            self.assertIsNone(core.check_release('1.0.0'))

    def test_stable_asset_and_prerelease(self):
        data = release()
        with patch.object(core.requests, 'get', return_value=Response(data)):
            found = core.check_release('1.0.0')
        self.assertEqual(found['version'], 'v1.1.0')
        self.assertEqual(found['asset']['sha256'], 'a' * 64)
        data['prerelease'] = True
        with patch.object(core.requests, 'get', return_value=Response(data)):
            self.assertIsNone(core.check_release('1.0.0'))

    def test_a_release_without_the_installer_still_reports_but_is_not_installable(self):
        data = release()
        data['assets'][0]['name'] = 'something-else.zip'
        with patch.object(core.requests, 'get', return_value=Response(data)):
            found = core.check_release('1.0.0')
        self.assertEqual(found['version'], 'v1.1.0')
        self.assertIsNone(found['asset'])

    def test_unverified_or_wrong_host_asset_is_not_installable(self):
        for field, value in [('digest', None), ('browser_download_url', 'https://example.com/evil.exe')]:
            data = release()
            data['assets'][0][field] = value
            with patch.object(core.requests, 'get', return_value=Response(data)):
                self.assertIsNone(core.check_release('1.0.0')['asset'])

    def test_download_success_cancel_corruption_and_oversize(self):
        asset = {'url': 'https://github.com/file', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / core.ASSET_NAME
            with patch.object(core.requests, 'get', return_value=Response(chunks=[b'a', b'bc'])):
                core.download_release(asset, path)
            self.assertEqual(path.read_bytes(), b'abc')
            for chunks, cancelled in [([b'abc'], lambda: True), ([b'xyz'], lambda: False), ([b'abcd'], lambda: False), ([b'ab'], lambda: False)]:
                with patch.object(core.requests, 'get', return_value=Response(chunks=chunks)):
                    with self.assertRaises(Exception): core.download_release(asset, path, cancelled=cancelled)
                self.assertFalse(path.exists())

    def test_download_from_an_unexpected_host_is_refused(self):
        asset = {'url': 'https://github.com/file', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
        response = Response(chunks=[b'abc'])
        response.url = 'https://example.com/file'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / core.ASSET_NAME
            with patch.object(core.requests, 'get', return_value=response):
                with self.assertRaises(ValueError): core.download_release(asset, path)
            self.assertFalse(path.exists())

    def test_installer_runs_quietly_and_closes_the_old_copy(self):
        command = core.installer_command(Path('C:/temp/Parallel-Setup.exe'))
        self.assertTrue(command[0].endswith('Parallel-Setup.exe'))
        self.assertIn('/SILENT', command)
        self.assertIn('/CLOSEAPPLICATIONS', command)

    def test_automatic_check_is_not_due_until_the_interval_passes(self):
        from datetime import date
        today = date(2026, 11, 25)
        self.assertTrue(core.automatic_check_due('daily', '2026-11-24', today))
        self.assertFalse(core.automatic_check_due('daily', '2026-11-25', today))
        self.assertFalse(core.automatic_check_due('weekly', '2026-11-20', today))
        self.assertFalse(core.automatic_check_due('never', '', today))


if __name__ == '__main__':
    unittest.main()
