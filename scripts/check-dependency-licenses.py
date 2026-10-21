"""Verify installed locked-distribution notice inventory; not legal certification."""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import re


def notice_files(dist):
    result = []
    for item in dist.files or []:
        name = Path(str(item)).name.lower()
        if (re.fullmatch(r'(?:licen[cs]es?|copying|notice)(?:[._-].*)?', name)
                and Path(name).suffix not in {'.py', '.pyc', '.pyo'}):
            path = dist.locate_file(item)
            if path.is_file():
                result.append({'file': str(item), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return sorted(result, key=lambda item: item['file'])


def verify(root, write=False):
    root = Path(root)
    lock = dict(re.findall(r'^([\w.-]+)==([^ ]+)',
                          (root/'requirements-macos-arm64-py314.lock').read_text(), re.M))
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
    locked = {normalize(k): v for k, v in lock.items()}
    path = root/'docs/reference/dependency-licenses.json'
    inventory = json.loads(path.read_text())
    entries = inventory['distributions']
    if {normalize(e['distribution']) for e in entries} != set(locked):
        raise ValueError('Inventory must cover exactly the locked distributions')
    changed = []
    for entry in entries:
        dist = distribution(entry['distribution'])
        version = locked[normalize(entry['distribution'])]
        if dist.version != version or entry['locked_version'] != version or entry['installed_version'] != version:
            raise ValueError('Installed/locked/inventoried version mismatch: ' + entry['distribution'])
        notices = notice_files(dist)
        if sorted(entry['license_files'], key=lambda item: item['file']) != notices:
            changed.append(entry['distribution'])
            entry['license_files'] = notices
    if changed and write:
        path.write_text(json.dumps(inventory, indent=2) + '\n')
    elif changed:
        raise ValueError('Notice inventory differs: ' + ', '.join(changed))
    return {'status': 'passed', 'distributions': len(entries), 'updated': changed,
            'scope': 'Notice file presence and hashes; not license compatibility or security certification'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Refresh notice hashes from the reviewed locked environment')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root, args.write)))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
