#!/usr/bin/env python3
"""Check release versions locally before a push; never changes files or Git state."""
import ast
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r'\d+\.\d+\.\d+')
VERSION_FILES = ('pyproject.toml', 'src/icloud_for_agents/version.py', 'README.md', 'CHANGELOG.md')


def version_tuple(value):
    if not isinstance(value, str) or VERSION.fullmatch(value) is None:
        raise ValueError('Use a stable MAJOR.MINOR.PATCH release version.')
    return tuple(map(int, value.split('.')))


def version_from_text(read):
    package = tomllib.loads(read('pyproject.toml'))['project']['version']
    version_tuple(package)
    module = ast.parse(read('src/icloud_for_agents/version.py'))
    runtime = next((ast.literal_eval(node.value) for node in module.body
                    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id == '__version__' for target in node.targets)), None)
    readme = re.search(r'^\*\*Version (\d+\.\d+\.\d+)\*\*$', read('README.md'), re.M)
    changelog = re.search(r'^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$', read('CHANGELOG.md'), re.M)
    if not readme or not changelog or {package, runtime, readme[1], changelog[1]} != {package}:
        raise ValueError('Update pyproject.toml, the runtime version, README version and first dated changelog entry together.')
    return package


def project_version(root):
    return version_from_text(lambda path: (root / path).read_text())


def validate_change_version(current, released, paths):
    current_tuple, released_tuple = version_tuple(current), version_tuple(released)
    if current_tuple < released_tuple:
        raise ValueError('The project version is older than the comparison version.')
    functional = any(path.startswith(('src/', 'scripts/', 'deployment/'))
                     or path in {'pyproject.toml', 'requirements-macos-arm64-py314.lock', 'MANIFEST.in'}
                     for path in paths)
    if functional and current_tuple <= released_tuple:
        raise ValueError('Functional changes need a version bump and dated changelog entry before pushing.')


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.splitlines()


def committed_version(ref):
    return version_from_text(lambda path: '\n'.join(git('show', f'{ref}:{path}')))


def check_pre_push(remote_ref='origin/main'):
    try:
        git('rev-parse', '--verify', remote_ref + '^{commit}')
    except subprocess.SubprocessError as exc:
        raise ValueError(f'Missing remote-tracking ref {remote_ref}. Fetch the intended remote branch before the pre-push check.') from exc
    try:
        git('merge-base', '--is-ancestor', remote_ref, 'HEAD')
    except subprocess.SubprocessError as exc:
        raise ValueError(f'{remote_ref} is not an ancestor of HEAD. Fetch and reconcile the branch before pushing.') from exc
    dirty = git('diff', '--name-only', 'HEAD', '--', *VERSION_FILES)
    if dirty:
        raise ValueError('Commit the release version references before pushing: ' + ', '.join(dirty))
    current = committed_version('HEAD')
    remote = committed_version(remote_ref)
    paths = git('diff', '--name-only', remote_ref + '..HEAD', '--')
    try:
        validate_change_version(current, remote, paths)
    except ValueError as exc:
        raise ValueError(f'Outgoing commits compared with {remote_ref}: {exc}') from exc
    return current, remote_ref


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-push', action='store_true', help='Validate committed HEAD against the local remote-tracking branch.')
    parser.add_argument('--remote-ref', default='origin/main', help='Local remote-tracking ref for --pre-push (default: origin/main).')
    args = parser.parse_args()
    try:
        if args.pre_push:
            current, base = check_pre_push(args.remote_ref)
            print(f'Pre-push release check passed: committed {current}; compared with {base}.')
            return 0
        current = project_version(ROOT)
        tags = [tag for tag in git('tag', '--merged', 'HEAD', '--list', 'v*')
                if VERSION.fullmatch(tag[1:])]
        if not tags:
            raise ValueError('No reachable release tags found. Fetch release tags before checking the next version.')
        base = max(tags, key=lambda tag: version_tuple(tag[1:]))
        paths = set(git('diff', '--name-only', base, '--'))
        paths.update(git('ls-files', '--others', '--exclude-standard'))
        validate_change_version(current, base[1:], paths)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print('Release check failed: ' + str(exc), file=sys.stderr)
        return 1
    print(f'Release check passed: {current}; latest reachable release {base}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
