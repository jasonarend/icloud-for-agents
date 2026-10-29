"""Native attachment export with synthetic provider metadata and owned files."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icloud_for_agents import native_reminder_files as files
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_read import NativeReader

SOURCE='11111111-1111-4111-8111-111111111111'
LIST='22222222-2222-4222-8222-222222222222'
ITEM='33333333-3333-4333-8333-333333333333'
ATTACHMENT='44444444-4444-4444-8444-444444444444'
RAW=b'Synthetic stored attachment representation only.'
LEAF=hashlib.sha512(RAW).hexdigest()+'.png'


class Reader:
    def __init__(self):
        self.item={'id':ITEM,'source_id':SOURCE,'list_id':LIST,'modified':'synthetic',
                   'native_metadata':{'attachments':[{'id':ATTACHMENT,'kind':'image','uti':'public.png'}]}}
        self.file={'protocol_version':1,'id':ITEM,'source_id':SOURCE,'list_id':LIST,
                   'attachment_id':ATTACHMENT,'kind':'image','uti':'public.png','file_leaf':LEAF,
                   'file_size':len(RAW),'data_mutated':False,'download_requested':False}
        self.calls=[]
    def read(self,action,**fields):
        self.calls.append(action)
        if action=='get':
            assert fields=={'id':ITEM,'include_native_attachments':True}
            return {'item':copy.deepcopy(self.item)}
        assert action=='attachment_file' and fields=={'id':ITEM,'attachment_id':ATTACHMENT}
        return copy.deepcopy(self.file)


class ReminderFileTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.file=self.root/'Container_v1/Files'/('Account-'+SOURCE)/'Attachments'/LEAF
        self.file.parent.mkdir(parents=True);self.file.write_bytes(RAW);self.file.chmod(0o600)
        self.reader=Reader()
        self.env={'HOME':str(self.root),'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_ENABLE_REMINDERS':'1',
                  'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}})}
        for p in (patch.dict(os.environ,self.env,clear=True),patch.object(files,'group_root',return_value=self.root)):
            p.start();self.addCleanup(p.stop)
    def export(self,maximum=1048576):return files.export(ITEM,ATTACHMENT,maximum,reader=self.reader)

    def test_exact_stored_bytes_hash_and_representation_without_paths(self):
        v=self.export();self.assertEqual(base64.b64decode(v['data']),RAW);self.assertEqual(v['sha256'],hashlib.sha256(RAW).hexdigest())
        self.assertEqual(v['size'],len(RAW));self.assertFalse(v['original_file']);self.assertFalse(v['complete_backup']);self.assertFalse(v['download_attempted']);self.assertFalse(v['data_mutated'])
        self.assertNotIn(LEAF,json.dumps(v));self.assertEqual(self.file.read_bytes(),RAW);self.assertEqual(self.reader.calls,['get','attachment_file','attachment_file','get'])

    def test_ids_bounds_disabled_and_opt_in_before_read(self):
        with patch.object(self.reader,'read',side_effect=AssertionError('No provider read')):
            for identity,attachment,maximum in [(ITEM+'/../secret',ATTACHMENT,1),(ITEM,'not-an-id',1),(ITEM,ATTACHMENT,True),(ITEM,ATTACHMENT,0),(ITEM,ATTACHMENT,1048577)]:
                with self.assertRaises(SafetyError):files.export(identity,attachment,maximum,reader=self.reader)
            for env in ({'ICLOUD_MAIL_ACCESS':'disabled'},{'ICLOUD_ENABLE_REMINDERS':'0'},{'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE]}})}):
                with patch.dict(os.environ,env),self.assertRaises(SafetyError):self.export()

    def test_descriptor_identity_scope_size_encoding_and_unknown_fields_refuse_before_file(self):
        for update in ({'id':SOURCE},{'attachment_id':ITEM},{'source_id':LIST},{'list_id':'other'},{'kind':'url'},{'uti':'\ud800'},{'file_leaf':'../../secret'},{'file_size':True},{'file_size':1048577},{'download_requested':True},{'data_mutated':True},{'extra':'unrecognized'}):
            old=copy.deepcopy(self.reader.file);self.reader.file.update(update)
            with self.subTest(update=update),patch.object(files,'_bytes',side_effect=AssertionError('No file read')),self.assertRaises(SafetyError):self.export()
            self.reader.file=old

    def test_provider_inventory_and_file_disagreement_refuse(self):
        for change in ({'attachments':[]},{'attachments':[{'id':ATTACHMENT,'kind':'file','uti':'public.png'}]}, {'attachments':[{'id':ATTACHMENT,'kind':'image','uti':'other'}]}):
            self.reader.item['native_metadata']=change
            with patch.object(files,'_bytes',side_effect=AssertionError('No file read')),self.assertRaises(SafetyError):self.export()

    def test_bound_size_digest_symlink_hardlink_fifo_and_permissions(self):
        with self.assertRaises(SafetyError):self.export(maximum=len(RAW)-1)
        outside=self.root/'outside';outside.write_bytes(RAW)
        for case in ('symlink','hardlink','fifo','writable','wrongbytes','truncated'):
            self.file.unlink()
            if case=='symlink':self.file.symlink_to(outside)
            elif case=='hardlink':os.link(outside,self.file)
            elif case=='fifo':os.mkfifo(self.file)
            else:
                self.file.write_bytes(b'x'*len(RAW) if case=='wrongbytes' else RAW[:-1] if case=='truncated' else RAW)
                self.file.chmod(0o666 if case=='writable' else 0o600)
            with self.subTest(case=case),self.assertRaises(SafetyError):self.export()

    def test_symlink_or_writable_directory_refuses(self):
        account=self.file.parent.parent;destination=self.root/'elsewhere';account.rename(destination);account.symlink_to(destination,target_is_directory=True)
        with self.assertRaises(SafetyError):self.export()
        account.unlink();destination.rename(account);account.chmod(0o777)
        with self.assertRaises(SafetyError):self.export()

    def test_provider_drift_revocation_and_same_fd_changes_refuse(self):
        read=files._bytes
        def drift(*args):
            result=read(*args);self.reader.item['modified']='changed';return result
        with patch.object(files,'_bytes',side_effect=drift),self.assertRaises(SafetyError):self.export()
        def revoked(*args):
            result=read(*args);os.environ['ICLOUD_MAIL_ACCESS']='disabled';return result
        with patch.object(files,'_bytes',side_effect=revoked),self.assertRaises(SafetyError):self.export()
        os.environ['ICLOUD_MAIL_ACCESS']='read-only';read_fd=os.read;changed=False
        def change_file(fd,count):
            nonlocal changed
            data=read_fd(fd,count)
            if not changed:
                changed=True;info=self.file.stat();os.utime(self.file,ns=(info.st_atime_ns,info.st_mtime_ns+1000))
            return data
        with patch.object(files.os,'read',side_effect=change_file),self.assertRaises(SafetyError):self.export()

    def test_internal_descriptor_action_gates_helper_and_validates_response(self):
        with patch('platform.system',return_value='Darwin'),patch.object(NativeReader,'_dispatch',return_value=self.reader.file) as dispatch:
            self.assertEqual(NativeReader('reminders').read('attachment_file',id=ITEM,attachment_id=ATTACHMENT),self.reader.file)
            self.assertEqual(dispatch.call_count,1)
            for bad in ({'id':ITEM},{'id':ITEM,'attachment_id':ATTACHMENT,'max_bytes':1},{'id':ITEM,'attachment_id':'../secret'}):
                with self.assertRaises(SafetyError):NativeReader('reminders').read('attachment_file',**bad)
            self.assertEqual(dispatch.call_count,1)
            dispatch.return_value=dict(self.reader.file,file_leaf='bad')
            with self.assertRaises(SafetyError):NativeReader('reminders').read('attachment_file',id=ITEM,attachment_id=ATTACHMENT)
