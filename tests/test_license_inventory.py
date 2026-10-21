import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('check_licenses', Path(__file__).resolve().parents[1]/'scripts/check-dependency-licenses.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class LicenseNoticeTests(unittest.TestCase):
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
