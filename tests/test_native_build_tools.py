"""Helper setup chooses one macOS toolchain and SDK without system changes."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class NativeBuildToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-build-tools-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.developer = self.root / 'Selected Developer'
        self.developer.mkdir()
        self.sdk = self.developer / 'MacOSX.sdk'
        self.sdk.mkdir()
        self.tools = {}
        for name in ('clang', 'swiftc'):
            tool = self.developer / name
            tool.write_text('synthetic compiler')
            tool.chmod(0o700)
            self.tools[name] = str(tool)
        self.module = runpy.run_path(str(ROOT / 'scripts/build-native-helper.py'))
        self.calls = []

    def run_tool(self, command, **kwargs):
        options = dict(kwargs)
        if 'env' in options:
            options['env'] = dict(options['env'])
        self.calls.append((list(command), options))
        if command == ['/usr/bin/xcode-select', '--print-path']:
            return SimpleNamespace(stdout=str(self.developer) + '\n')
        if command == ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path']:
            return SimpleNamespace(stdout=str(self.sdk) + '\n')
        if command[:4] == ['/usr/bin/xcrun', '--sdk', 'macosx', '--find']:
            return SimpleNamespace(stdout=self.tools[command[-1]] + '\n')
        if '-o' in command:
            Path(command[command.index('-o') + 1]).write_bytes(b'synthetic output')
        return SimpleNamespace(returncode=0)

    def test_selected_xcode_ignores_stale_sdk_and_freezes_developer_selection(self):
        original = {'SDKROOT': '/stale/CommandLineTools/MacOSX.sdk', 'PATH': '/synthetic', 'TOOLCHAINS': 'selected-toolchain'}
        with patch.dict(os.environ, original, clear=True), patch('subprocess.run', side_effect=self.run_tool):
            clang, swift, sdk, environment = self.module['resolve_build_tools']()
            self.assertEqual(dict(os.environ), original)
        self.assertEqual((clang, swift, sdk), (self.tools['clang'], self.tools['swiftc'], str(self.sdk)))
        self.assertEqual(environment['SDKROOT'], str(self.sdk))
        self.assertEqual(environment['DEVELOPER_DIR'], str(self.developer))
        self.assertEqual(environment['TOOLCHAINS'], 'selected-toolchain')
        for command, options in self.calls:
            self.assertNotIn('SDKROOT', options['env'])
            self.assertTrue(options['check'])
            self.assertEqual(options['timeout'], 20)
            if command[0] == '/usr/bin/xcrun':
                self.assertEqual(command[1:3], ['--sdk', 'macosx'])
                self.assertEqual(options['env']['DEVELOPER_DIR'], str(self.developer))

    def test_explicit_command_line_tools_selection_is_preserved(self):
        explicit = str(self.root / 'CommandLineTools')
        with patch.dict(os.environ, {'DEVELOPER_DIR': explicit, 'SDKROOT': '/unrelated.sdk'}, clear=True), patch('subprocess.run', side_effect=self.run_tool):
            environment = self.module['resolve_build_tools']()[3]
        self.assertEqual(environment['DEVELOPER_DIR'], explicit)
        self.assertFalse(any(command[0] == '/usr/bin/xcode-select' for command, _ in self.calls))
        self.assertTrue(all(options['env']['DEVELOPER_DIR'] == explicit for _, options in self.calls))

    def test_every_compilation_receives_resolved_tools_sdk_and_environment(self):
        bundle = self.root / 'iCloud for Agents.app'
        with patch.dict(os.environ, {'SDKROOT': '/stale.sdk'}, clear=True), patch.object(sys, 'argv', ['build-native-helper.py', '--output', str(bundle)]), patch('subprocess.run', side_effect=self.run_tool), contextlib.redirect_stdout(io.StringIO()):
            self.module['main']()
        compile_calls = [(command, options) for command, options in self.calls if command[0] in self.tools.values()]
        self.assertEqual(len(compile_calls), 4)
        for command, options in compile_calls:
            flag = '-isysroot' if command[0] == self.tools['clang'] else '-sdk'
            self.assertEqual(command[command.index(flag) + 1], str(self.sdk))
            self.assertEqual(options['env']['SDKROOT'], str(self.sdk))
            self.assertEqual(options['env']['DEVELOPER_DIR'], str(self.developer))
        self.assertTrue((bundle / 'Contents/MacOS/icloud-agents-native').is_file())
        self.assertTrue((bundle / 'Contents/MacOS/icloud-agents-image-text').is_file())

    def test_unavailable_tools_stop_before_creating_output_without_fallback(self):
        bundle = self.root / 'Unavailable.app'
        for failure in (FileNotFoundError('xcrun'), subprocess.CalledProcessError(1, 'xcrun'), subprocess.TimeoutExpired('xcrun', 20)):
            with self.subTest(failure=type(failure).__name__), patch.object(sys, 'argv', ['build-native-helper.py', '--output', str(bundle)]), patch('subprocess.run', side_effect=failure) as run, contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):
                    self.module['main']()
                self.assertEqual(run.call_count, 1)
                self.assertFalse(bundle.exists())
                self.assertIn('Install or select a complete Xcode or Command Line Tools', errors.getvalue())

    def test_missing_sdk_or_compiler_is_an_actionable_error(self):
        for absent in ('sdk', 'clang', 'swiftc'):
            with self.subTest(absent=absent):
                target = self.sdk if absent == 'sdk' else Path(self.tools[absent])
                moved = target.with_name(target.name + '.saved')
                target.rename(moved)
                try:
                    with patch('subprocess.run', side_effect=self.run_tool), self.assertRaisesRegex(self.module['BuildToolsError'], 'missing its macOS SDK or compilers'):
                        self.module['resolve_build_tools']()
                finally:
                    moved.rename(target)

    def test_malformed_discovery_path_is_refused(self):
        for value in ('', 'relative/sdk', '/first/sdk\n/second/sdk'):
            with self.subTest(value=value), patch('subprocess.run', return_value=SimpleNamespace(stdout=value)), self.assertRaisesRegex(self.module['BuildToolsError'], 'invalid build path'):
                self.module['resolve_build_tools']()


if __name__ == '__main__':
    unittest.main()
