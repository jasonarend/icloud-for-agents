#!/usr/bin/env python3
"""Build the fixed native helper; building never grants access or activates writes."""
import argparse
import os
import plistlib
import platform
import subprocess
import shutil
from pathlib import Path


class BuildToolsError(ValueError):
    """The selected Apple developer installation cannot build the helper."""


def resolve_build_tools():
    """Keep compilers and SDK in one selected developer environment.

    A separately updated Command Line Tools SDK can become the implicit default
    even while xcode-select points at an older full Xcode installation. Resolve
    macOS explicitly and set it for every compiler instead of using that default.
    DEVELOPER_DIR and TOOLCHAINS remain available for deliberate local selection.
    """
    environment = dict(os.environ)
    environment.pop('SDKROOT', None)

    def query(command):
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True,
                                    timeout=20, env=environment)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BuildToolsError('The selected Apple developer tools are unavailable. '
                                 'Install or select a complete Xcode or Command Line Tools installation, then try setup again.') from exc
        value = result.stdout.strip()
        if not value or '\n' in value or not Path(value).is_absolute():
            raise BuildToolsError('The selected Apple developer tools returned an invalid build path.')
        return value

    # Freeze the selected installation for this build without changing the
    # machine's xcode-select setting or the caller's environment.
    if not environment.get('DEVELOPER_DIR'):
        environment['DEVELOPER_DIR'] = query(['/usr/bin/xcode-select', '--print-path'])
    sdk = query(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'])
    clang = query(['/usr/bin/xcrun', '--sdk', 'macosx', '--find', 'clang'])
    swift = query(['/usr/bin/xcrun', '--sdk', 'macosx', '--find', 'swiftc'])
    if not Path(sdk).is_dir() or any(not Path(tool).is_file() or not os.access(tool, os.X_OK)
                                   for tool in (clang, swift)):
        raise BuildToolsError('The selected Apple developer installation is missing its macOS SDK or compilers. '
                             'Repair that installation, then try setup again.')
    environment['SDKROOT'] = sdk
    return clang, swift, sdk, environment


def sign(target, identifier, identity):
    command = ['/usr/bin/codesign', '--force', '--sign', identity, '--identifier', identifier]
    if identity != '-':
        # This is a local build, not a notarization or publication workflow.
        command.append('--timestamp=none')
    subprocess.run([*command, str(target)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('build/native/iCloud for Agents.app'))
    parser.add_argument('--sign-identity', default='-',
                        help='Explicit local Keychain signing identity; default is ad hoc. Never grants permissions or publishes.')
    args = parser.parse_args()
    if (not args.sign_identity.strip() or len(args.sign_identity)>1000
            or args.sign_identity.startswith('-') and args.sign_identity!='-'
            or any(ord(c)<32 or 127<=ord(c)<=159 for c in args.sign_identity)):
        parser.error('Use an explicit nonempty local signing identity or the default ad hoc identity')
    root = Path(__file__).resolve().parents[1]
    bundle = args.output.resolve()
    if bundle.suffix != '.app':
        parser.error('The helper output must be a macOS .app bundle')
    executable = bundle / 'Contents/MacOS/icloud-agents-native'
    if bundle.exists():
        parser.error('Existing helper is preserved; use a new output path for a reviewed rebuild')
    architecture = {'arm64': 'arm64', 'x86_64': 'x86_64'}.get(platform.machine())
    if architecture is None:
        parser.error('Unsupported native build architecture')
    try:
        clang, swift, sdk, build_environment = resolve_build_tools()
    except BuildToolsError as exc:
        parser.error(str(exc))
    executable.parent.mkdir(parents=True)
    info = {'CFBundleIdentifier': 'com.jasonarend.icloud-for-agents.native',
            'CFBundleName': 'iCloud for Agents', 'CFBundleDisplayName': 'iCloud for Agents', 'CFBundleExecutable': executable.name,
            'CFBundleVersion': '3', 'CFBundlePackageType': 'APPL', 'LSUIElement': True,
            'NSAppleEventsUsageDescription': 'Read the Notes accounts you select for your AI assistant. Changes require separate application authorization.',
            'NSAppDataUsageDescription': 'Access local Notes app data for explicitly enabled native Notes features. The server limits operations to selected sources and approved changes.',
            'NSRemindersFullAccessUsageDescription': 'Read only the Reminders sources you select for your AI assistant. Application writes require separate authorization.'}
    resources = bundle / 'Contents/Resources'
    resources.mkdir()
    for name in ('notes.js','notes-write.js'):
        shutil.copyfile(root / 'src/icloud_for_agents/native' / name, resources / name)
    plist = bundle / 'Contents/Info.plist'
    plist.write_bytes(plistlib.dumps(info))
    source = root / 'src/icloud_for_agents/native/reminders.swift'
    native = root / 'src/icloud_for_agents/native'
    objects=[]
    for name in ('notes-model','reminders-model'):
        object_file=bundle/'Contents'/(name+'.o');objects.append(object_file)
        subprocess.run([clang,'-isysroot',sdk,'-fobjc-arc','-arch',architecture,'-mmacosx-version-min=14.0','-c',str(native/(name+'.m')),'-o',str(object_file)],check=True,env=build_environment)
    subprocess.run([swift,'-sdk',sdk,str(source),*[str(p) for p in objects],'-import-objc-header',str(native/'bridge.h'),'-framework','CoreData','-lsqlite3','-target',architecture+'-apple-macos14.0','-o',str(executable),
                    '-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist','-Xlinker',str(plist)],check=True,env=build_environment)
    for path in objects:path.unlink()
    processor=executable.parent/'icloud-agents-image-text'
    subprocess.run([swift,'-sdk',sdk,str(native/'notes-ocr.swift'),'-target',architecture+'-apple-macos14.0','-o',str(processor)],check=True,env=build_environment)
    sign(processor, info['CFBundleIdentifier']+'.image-text', args.sign_identity)
    sign(bundle, info['CFBundleIdentifier'], args.sign_identity)
    print(executable)


if __name__ == '__main__':
    main()
