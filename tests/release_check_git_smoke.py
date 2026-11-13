"""Exercise release checks against an isolated Git history without network access."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseCheckGitSmoke(unittest.TestCase):
    def test_pre_push_cli_with_real_git_reuses_only_unreleased_versions(self):
        with tempfile.TemporaryDirectory(prefix='release-check-git-') as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'src/icloud_for_agents').mkdir(parents=True)
            shutil.copyfile(ROOT / 'scripts/check-release.py', root / 'scripts/check-release.py')

            def git(*args):
                return subprocess.run(['git', '-c', 'user.name=Synthetic Test',
                    '-c', 'user.email=synthetic@example.invalid', '-c', 'commit.gpgsign=false',
                    '-c', 'tag.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
                    cwd=root, check=True, capture_output=True, text=True)

            def version(value):
                (root / 'pyproject.toml').write_text(f'[project]\nversion="{value}"\n')
                (root / 'src/icloud_for_agents/version.py').write_text(f'__version__="{value}"\n')
                (root / 'README.md').write_text(f'**Version {value}**\n')

            def commit():
                git('add', '.')
                git('commit', '-m', 'Synthetic change')

            def check(expected, message=''):
                result = subprocess.run([sys.executable, str(root / 'scripts/check-release.py'), '--pre-push'],
                    cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)

            git('init', '-b', 'main')
            version('2.1.0')
            (root / 'CHANGELOG.md').write_text('## 2.1.0 — 2026-11-08\n')
            commit()
            git('tag', 'v2.1.0')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            version('2.1.1')
            source = root / 'src/icloud_for_agents/server.py'
            source.write_text('first = True\n')
            commit()
            check(0)
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            source.write_text('second = True\n')
            commit()
            check(0)
            git('tag', 'v2.1.1')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            source.write_text('third = True\n')
            commit()
            check(1, 'version bump')
            git('checkout', '-b', 'docs', 'v2.1.1')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            (root / 'guide.md').write_text('Documentation update\n')
            commit()
            check(0)
            version('2.1.0')
            commit()
            check(1, 'older')
            version('2.1.1')
            commit()
            git('tag', '-d', 'v2.1.0', 'v2.1.1')
            check(1, 'Fetch release tags')


if __name__ == '__main__':
    unittest.main()
