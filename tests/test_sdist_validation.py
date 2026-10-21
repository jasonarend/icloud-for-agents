"""Exercise source-archive validation against real synthetic tar archives."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('check_sdist', Path(__file__).resolve().parents[1]/'scripts/check-sdist.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class SourceDistributionTests(unittest.TestCase):
    def archive(self, root, omit=None, extra=None):
        archive = root/'candidate.tar.gz'
        names = ['README.md', 'LICENSE', 'NOTICE.md', 'SECURITY.md', 'pyproject.toml',
                 'MANIFEST.in', 'requirements-macos-arm64-py314.lock',
                 'tests/run_offline.py', 'tests/guarded_stdio_server.py']
        for name in names:
            path = root/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text('synthetic')
        with tarfile.open(archive, 'w:gz') as out:
            for name in names:
                if name != omit:
                    out.add(root/name, arcname='package/'+name)
            if extra:
                info = tarfile.TarInfo(extra);info.size = 1;out.addfile(info, io.BytesIO(b'x'))
        return archive

    def test_all_required_files_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(checker.check_archive(self.archive(root), root)['status'], 'passed')

    def test_missing_runner_or_fixture_fails(self):
        for omitted in ('tests/run_offline.py', 'tests/guarded_stdio_server.py'):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaisesRegex(ValueError, omitted):
                    checker.check_archive(self.archive(root, omit=omitted), root)

    def test_private_and_traversing_paths_fail(self):
        for extra in ('package/local/settings.json', 'package/../outside'):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(ValueError):
                    checker.check_archive(self.archive(root, extra=extra), root)
