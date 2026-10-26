"""Only exact fixed-helper pre-dispatch evidence can establish a known refusal."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from icloud_for_agents.core import SafetyError,NativeOperationRefused,digest
from icloud_for_agents.native_read import NativeReader


class NativeRefusalTests(unittest.TestCase):
    def test_notes_refusal_binds_exact_request_and_sanitizes_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            helper=Path(directory)/'Helper.app/Contents/MacOS/helper';helper.parent.mkdir(parents=True)
            helper.write_text('synthetic');helper.chmod(0o700)
            request={'protocol_version':1,'action':'apply','source_ids':['selected'],'change':{'operation':'invalid'}}
            response={'protocol_version':1,'error':'notes_operation_failed','category':'snapshot_changed','mutation_dispatched':False}
            with patch('icloud_for_agents.native_app.application_request',return_value=response),self.assertRaises(NativeOperationRefused) as caught:
                NativeReader('notes')._dispatch(request,{'helper_path':str(helper)})
            self.assertEqual(caught.exception.evidence['request_sha256'],digest(request))
            self.assertEqual(caught.exception.evidence['helper_response_sha256'],digest(response))
            self.assertIn('snapshot_changed',str(caught.exception))
    def test_after_dispatch_missing_or_nonboolean_flags_never_mean_no_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            helper=Path(directory)/'helper';helper.write_text('synthetic');helper.chmod(0o700)
            for value in (True,None,0):
                response={'protocol_version':1,'error':'notes_operation_failed','mutation_dispatched':value}
                with patch('icloud_for_agents.native_app.application_request',return_value=response),self.assertRaises(SafetyError) as caught:
                    NativeReader('notes')._dispatch({'action':'apply'},{'helper_path':str(helper)})
                self.assertNotIsInstance(caught.exception,NativeOperationRefused)
    def test_reminders_refusal_is_typed_only_for_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            helper=Path(directory)/'helper';helper.write_text('synthetic');helper.chmod(0o700)
            def process(command,**kwargs):
                kwargs['stdout'].write(json.dumps({'protocol_version':1,'error':'not_found_in_scope','mutation_dispatched':False}).encode())
                return SimpleNamespace(returncode=0)
            with patch('subprocess.run',side_effect=process):
                with self.assertRaises(NativeOperationRefused):NativeReader('reminders')._dispatch({'action':'apply'},{'helper_path':str(helper)})
                with self.assertRaises(SafetyError) as caught:NativeReader('reminders')._dispatch({'action':'get'},{'helper_path':str(helper)})
                self.assertNotIsInstance(caught.exception,NativeOperationRefused)
