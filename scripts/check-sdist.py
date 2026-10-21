"""Verify a built source archive contains the documented validation workflow."""
import argparse
from pathlib import Path, PurePosixPath
import tarfile


def check_archive(archive, root):
    root = Path(root)
    required = {'README.md', 'LICENSE', 'NOTICE.md', 'SECURITY.md', 'pyproject.toml',
                'MANIFEST.in', 'requirements-macos-arm64-py314.lock'}
    for directory in ('src', 'scripts', 'tests', 'docs', 'third_party', 'deployment'):
        required.update(p.relative_to(root).as_posix() for p in (root/directory).rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    forbidden = {'local', 'runtime', 'tools', '.venv', '.git', 'build', 'dist', '__pycache__'}
    files = set()
    with tarfile.open(archive, 'r:*') as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe archive path')
            if member.issym() or member.islnk():
                raise ValueError('Source archive must not contain links')
            if forbidden.intersection(path.parts[1:]):
                raise ValueError('Private/generated path in source archive')
            if member.isfile():
                name = PurePosixPath(*path.parts[1:]).as_posix()
                if name in files:
                    raise ValueError('Duplicate archive member')
                files.add(name)
    missing = sorted(required - files)
    if missing:
        raise ValueError('Source archive missing required files: ' + ', '.join(missing))
    return {'status': 'passed', 'required_files': len(required), 'archive_files': len(files)}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(check_archive(args.archive, args.source_root)))
    except (ValueError, tarfile.TarError) as exc:
        parser.exit(1, str(exc) + '\n')
