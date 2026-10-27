#!/usr/bin/env python3
"""Build the fixed native helper; building never grants access or activates writes."""
import argparse
import plistlib
import platform
import subprocess
import shutil
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
            'CFBundleVersion': '3', 'CFBundlePackageType': 'APPL', 'LSUIElement': True,
            'NSAppleEventsUsageDescription': 'Read the Notes accounts you select for your AI assistant. Changes require separate application authorization.',
            'NSRemindersFullAccessUsageDescription': 'Read only the Reminders sources you select for your AI assistant. Application writes require separate authorization.'}
    resources = bundle / 'Contents/Resources'
    resources.mkdir()
    for name in ('notes.js','notes-write.js'):
        shutil.copyfile(root / 'src/icloud_for_agents/native' / name, resources / name)
    plist = bundle / 'Contents/Info.plist'
    plist.write_bytes(plistlib.dumps(info))
    source = root / 'src/icloud_for_agents/native/reminders.swift'
    architecture = {'arm64': 'arm64', 'x86_64': 'x86_64'}.get(platform.machine())
    if architecture is None:
        parser.error('Unsupported native build architecture')
    native = root / 'src/icloud_for_agents/native'
    model_object = bundle / 'Contents' / 'notes-model.o'
    subprocess.run(['/usr/bin/clang','-fobjc-arc','-arch',architecture,'-mmacosx-version-min=14.0','-c',str(native/'notes-model.m'),'-o',str(model_object)],check=True)
    subprocess.run(['/usr/bin/swiftc', str(source), str(model_object), '-import-objc-header', str(native/'notes-model.h'), '-framework', 'CoreData', '-target', architecture + '-apple-macos14.0', '-o', str(executable),
                    '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist', '-Xlinker', str(plist)], check=True)
    model_object.unlink()
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', info['CFBundleIdentifier'], str(bundle)], check=True)
    print(executable)


if __name__ == '__main__':
    main()
