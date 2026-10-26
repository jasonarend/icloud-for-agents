#!/usr/bin/env python3
"""Build the repository-owned read-only helper; never requests permissions."""
import argparse
import plistlib
import platform
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('build/native/iCloudAgentsHelper.app'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = args.output.resolve()
    executable = bundle / 'Contents/MacOS/icloud-agents-native'
    if bundle.exists():
        parser.error('Existing helper is preserved; use a new output path for a reviewed rebuild')
    executable.parent.mkdir(parents=True)
    info = {'CFBundleIdentifier': 'com.jasonarend.icloud-for-agents.native',
            'CFBundleName': 'iCloud for Agents Native', 'CFBundleExecutable': executable.name,
            'CFBundleVersion': '2', 'CFBundlePackageType': 'APPL', 'LSUIElement': True,
            'NSRemindersFullAccessUsageDescription': 'Read only the Reminders sources you select for your AI assistant. Application writes require separate authorization.'}
    plist = bundle / 'Contents/Info.plist'
    plist.write_bytes(plistlib.dumps(info))
    source = root / 'src/icloud_for_agents/native/reminders.swift'
    architecture = {'arm64': 'arm64', 'x86_64': 'x86_64'}.get(platform.machine())
    if architecture is None:
        parser.error('Unsupported native build architecture')
    subprocess.run(['/usr/bin/swiftc', str(source), '-target', architecture + '-apple-macos14.0', '-o', str(executable),
                    '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist', '-Xlinker', str(plist)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', info['CFBundleIdentifier'], str(bundle)], check=True)
    print(executable)


if __name__ == '__main__':
    main()
