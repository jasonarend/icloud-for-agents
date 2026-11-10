"""Pre-push release checks catch missing bumps and mismatched published versions."""
from pathlib import Path
import io
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CHECK = runpy.run_path(str(ROOT / 'scripts/check-release.py'))


class ReleaseCheckTests(unittest.TestCase):
    def pre_push(self, head='2.1.1', remote='2.1.0', paths=(), dirty=(), mismatch=False):
        def read_ref(ref, path):
            version = head if ref == 'HEAD' else remote
            return {'pyproject.toml': f'[project]\nversion="{version}"',
                    'src/icloud_for_agents/version.py': f'__version__="{version}"',
                    'README.md': f'**Version {"0.0.0" if mismatch and ref == "HEAD" else version}**',
                    'CHANGELOG.md': f'## {version} — 2026-11-08'}[path].splitlines()
        def fake_git(*args):
            if args[0] == 'show':
                return read_ref(*args[1].split(':', 1))
            if args[0] == 'diff':
                return list(dirty if args[2] == 'HEAD' else paths)
            return []
        function = CHECK['check_pre_push']
        with patch.dict(function.__globals__, {'git': fake_git,
                        'project_version': lambda root: self.fail('Pre-push must inspect committed files')}):
            return function()

    def test_functional_changes_require_a_newer_version(self):
        for path in ('src/icloud_for_agents/server.py', 'scripts/build-native-helper.py',
                     'deployment/example.json', 'requirements-macos-arm64-py314.lock'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'version bump'):
                CHECK['validate_change_version']('2.1.0', '2.1.0', [path])
        CHECK['validate_change_version']('2.1.1', '2.1.0', ['src/icloud_for_agents/server.py'])

    def test_docs_and_tests_can_keep_current_version(self):
        CHECK['validate_change_version']('2.1.0', '2.1.0', ['README.md', 'docs/setup.md', 'tests/test_example.py'])
        with self.assertRaisesRegex(ValueError, 'older'):
            CHECK['validate_change_version']('2.0.0', '2.1.0', ['README.md'])

    def test_version_references_match_and_changelog_is_required_only_for_release(self):
        with tempfile.TemporaryDirectory(prefix='release-check-') as directory:
            root = Path(directory)
            (root / 'src/icloud_for_agents').mkdir(parents=True)
            (root / 'pyproject.toml').write_text('[project]\nversion="2.1.1"\n')
            (root / 'src/icloud_for_agents/version.py').write_text('__version__="2.1.1"\n')
            (root / 'README.md').write_text('# Example\n\n**Version 2.1.1**\n')
            (root / 'CHANGELOG.md').write_text('# Changelog\n\n## 2.1.1 — 2026-11-08\n')
            self.assertEqual(CHECK['project_version'](root), '2.1.1')
            for file, bad in [('README.md', '**Version 2.1.0**\n'),
                              ('src/icloud_for_agents/version.py', '__version__="2.1.0"\n')]:
                target = root / file
                original = target.read_text()
                target.write_text(bad)
                with self.subTest(file=file), self.assertRaisesRegex(ValueError, 'together'):
                    CHECK['project_version'](root)
                target.write_text(original)
            (root / 'CHANGELOG.md').write_text('## 2.1.0 — 2026-11-08\n')
            self.assertEqual(CHECK['project_version'](root), '2.1.1')
            with self.assertRaisesRegex(ValueError, 'changelog'):
                CHECK['project_version'](root, require_changelog=True)

    def test_local_commit_does_not_disable_default_or_pre_push_version_checks(self):
        function = CHECK['main']
        def fake_git(*args):
            if args[0] == 'tag':
                return ['v2.1.2']
            if args[0] == 'diff':
                return ['src/icloud_for_agents/server.py']
            return []
        with patch.dict(function.__globals__, {'git': fake_git, 'project_version': lambda *args, **kwargs: '2.1.2'}):
            for args, expected in [(['check-release.py', '--local-commit'], 0), (['check-release.py'], 1)]:
                with self.subTest(args=args), patch.object(sys, 'argv', args), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(function(), expected)
            with patch.object(sys, 'argv', ['check-release.py', '--local-commit', '--pre-push']), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    function()

    def test_pre_push_checks_committed_versions_against_remote_each_batch(self):
        self.assertEqual(self.pre_push(paths=['src/icloud_for_agents/server.py']), ('2.1.1', 'origin/main'))
        with self.assertRaisesRegex(ValueError, 'version bump'):
            self.pre_push(head='2.1.0', paths=['src/icloud_for_agents/server.py'])
        # A previously pushed but unreleased version cannot be reused for a new
        # functional batch merely because its release tag has not been created.
        with self.assertRaisesRegex(ValueError, 'version bump'):
            self.pre_push(head='2.1.1', remote='2.1.1', paths=['scripts/setup.py'])
        self.pre_push(head='2.1.0', paths=['docs/setup.md', 'tests/test_example.py'])

    def test_pre_push_refuses_uncommitted_bump_or_inconsistent_head(self):
        with self.assertRaisesRegex(ValueError, 'Commit the release version references'):
            self.pre_push(head='2.1.0', dirty=['pyproject.toml', 'README.md'])
        with self.assertRaisesRegex(ValueError, 'together'):
            self.pre_push(mismatch=True)

    def test_pre_push_missing_or_diverged_remote_is_actionable(self):
        function = CHECK['check_pre_push']
        with patch.dict(function.__globals__, {'git': lambda *args: (_ for _ in ()).throw(subprocess.CalledProcessError(1, 'git'))}):
            with self.assertRaisesRegex(ValueError, 'Fetch the intended remote branch'):
                function()
        def diverged(*args):
            if args[0] == 'merge-base':
                raise subprocess.CalledProcessError(1, 'git')
            return []
        with patch.dict(function.__globals__, {'git': diverged}):
            with self.assertRaisesRegex(ValueError, 'reconcile the branch'):
                function()


if __name__ == '__main__':
    unittest.main()
