import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('check_licenses', Path(__file__).resolve().parents[1]/'scripts/check-dependency-licenses.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class LicenseNoticeTests(unittest.TestCase):
    def test_only_reviewed_notice_hash_variants_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'requirements-macos-arm64-py314.lock').write_text('example==1 --hash=sha256:synthetic\n')
            name = 'example.dist-info/licenses/COPYING'
            notice = root/name;notice.parent.mkdir(parents=True);notice.write_bytes(b'reviewed\r\n')
            entry = {'distribution': 'example', 'locked_version': '1', 'installed_version': '1',
                     'license_files': [{'file': name, 'sha256': hashlib.sha256(b'reviewed\n').hexdigest(),
                                       'verified_variants': [{'sha256': hashlib.sha256(b'reviewed\r\n').hexdigest()}]}]}
            inventory = root/'docs/reference/dependency-licenses.json'
            inventory.parent.mkdir(parents=True);inventory.write_text(json.dumps({'distributions': [entry]}))
            dist = SimpleNamespace(version='1', files=[name], locate_file=lambda item: root/item)
            with patch.object(checker, 'distribution', return_value=dist):
                self.assertEqual(checker.verify(root)['status'], 'passed')
                notice.write_bytes(b'unreviewed changed text')
                with self.assertRaisesRegex(ValueError, 'Notice inventory differs'):
                    checker.verify(root)

    def test_both_spellings_and_notice_files_are_inventoried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ['pkg.dist-info/licenses/LICENCE', 'pkg.dist-info/licenses/LICENCE.md',
                     'pkg.dist-info/licenses/LICENSE.txt', 'pkg.dist-info/licenses/NOTICE',
                     'pkg/license.py', 'pkg/__pycache__/license.cpython-314.pyc']
            for name in names:
                target = root/name;target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(b'synthetic')
            dist = SimpleNamespace(files=names, locate_file=lambda item: root/item)
            result = checker.notice_files(dist)
            self.assertEqual({item['file'] for item in result}, set(names[:4]))
            self.assertTrue(all(item['sha256'] == hashlib.sha256(b'synthetic').hexdigest() for item in result))
