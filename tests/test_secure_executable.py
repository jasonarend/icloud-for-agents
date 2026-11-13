"""Credential launch identity tests using disposable executable bytes."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents import secure_executable as secure


class SecureExecutableTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.source=self.root/'shared';self.source.mkdir(mode=0o777);self.source.chmod(0o777)
        self.binary=self.source/'client';self.binary.write_bytes(b'reviewed executable');self.binary.chmod(0o700)
        self.digest=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.cache=self.root/'cache';self.cache.mkdir(mode=0o700)
        self.patcher=patch.object(secure,'_cache_directory',return_value=self.cache)
        self.patcher.start();self.addCleanup(self.patcher.stop)

    def test_snapshot_has_exact_reviewed_bytes_and_private_mode(self):
        staged=secure.stage_executable(self.binary,self.digest)
        self.assertEqual(staged,self.cache/self.digest)
        self.assertEqual(staged.read_bytes(),b'reviewed executable')
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode),0o700)
        self.binary.unlink();self.binary.write_bytes(b'replaced source')
        self.assertEqual(staged.read_bytes(),b'reviewed executable')

    def test_changed_source_and_unsafe_leaf_never_stage(self):
        self.binary.write_bytes(b'changed before staging')
        with self.assertRaises(secure.ExecutableSafetyError):secure.stage_executable(self.binary,self.digest)
        self.assertEqual(list(self.cache.iterdir()),[])
        self.binary.chmod(0o777)
        with self.assertRaises(secure.ExecutableSafetyError):secure.executable_digest(self.binary)
        self.binary.unlink();self.binary.symlink_to('/bin/sh')
        with self.assertRaises(OSError):secure.stage_executable(self.binary,self.digest)

    def test_invalid_checksum_cannot_select_cache_path(self):
        for value in ('../escape','0'*63,None):
            with self.subTest(value=value),self.assertRaises(secure.ExecutableSafetyError):
                secure.stage_executable(self.binary,value)
        self.assertEqual(list(self.cache.iterdir()),[])

    def test_copy_failure_removes_prepared_file(self):
        with patch.object(secure,'_unchanged',side_effect=secure.ExecutableSafetyError('changed')):
            with self.assertRaises(secure.ExecutableSafetyError):secure.stage_executable(self.binary,self.digest)
        self.assertEqual(list(self.cache.iterdir()),[])

    def test_trusted_paths_check_original_and_symlink_target_ancestors(self):
        safe=self.root/'private';safe.mkdir(mode=0o700)
        interpreter=safe/'python';interpreter.write_bytes(b'reviewed');interpreter.chmod(0o700)
        link=safe/'python-link';link.symlink_to(interpreter)
        self.assertEqual(secure.trusted_path(link,executable=True),interpreter.resolve())
        alias=self.source/'installation';alias.symlink_to(safe)
        with self.assertRaises(secure.ExecutableSafetyError):secure.trusted_path(alias,directory=True)
        link.unlink();link.symlink_to(self.binary)
        with self.assertRaises(secure.ExecutableSafetyError):secure.trusted_path(link,executable=True)

    def test_acl_write_grant_is_not_hidden_by_private_mode(self):
        with patch.object(secure,'_acl_allows_write',side_effect=lambda path,**kwargs:path.resolve()==self.cache.resolve()):
            with self.assertRaisesRegex(secure.ExecutableSafetyError,'ACL write grant'):
                secure.trusted_path(self.cache,directory=True)

    def test_nested_symlink_relay_cannot_hide_writable_ancestor(self):
        safe=self.root/'private';safe.mkdir(mode=0o700)
        actual=safe/'actual-python';actual.write_bytes(b'reviewed');actual.chmod(0o700)
        relay=self.source/'relay';relay.symlink_to(actual)
        inner=safe/'inner';inner.symlink_to(relay)
        outer=safe/'python';outer.symlink_to(inner)
        with self.assertRaisesRegex(secure.ExecutableSafetyError,'writable'):
            secure.trusted_path(outer,executable=True)

    def test_safe_relative_parent_links_follow_filesystem_semantics(self):
        safe=self.root/'private';safe.mkdir(mode=0o700)
        (safe/'bin').mkdir();(safe/'links').mkdir();(safe/'runtime').mkdir()
        actual=safe/'runtime/python';actual.write_bytes(b'reviewed');actual.chmod(0o700)
        (safe/'links/relay').symlink_to('../runtime/python')
        link=safe/'bin/python';link.symlink_to('../links/relay')
        self.assertEqual(secure.trusted_path(link,executable=True),actual.resolve())
        # A parent component after a directory symlink applies to the resolved
        # directory, rather than to the lexical link-containing directory.
        (safe/'runtime/deep').mkdir()
        (safe/'alias').symlink_to('runtime/deep')
        link.unlink();link.symlink_to('../alias/../python')
        self.assertEqual(secure.trusted_path(link,executable=True),actual.resolve())

    def test_symlink_loops_refuse_without_recursing_forever(self):
        safe=self.root/'private';safe.mkdir(mode=0o700)
        (safe/'first').symlink_to('second');(safe/'second').symlink_to('first')
        with self.assertRaisesRegex(secure.ExecutableSafetyError,'Unresolvable'):
            secure.trusted_path(safe/'first',executable=True)


if __name__=='__main__':unittest.main()
