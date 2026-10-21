import copy
import unittest
from test_mail_safety import FakeIMAP
from icloud_for_agents import core
from icloud_for_agents.reconcile import complete_archive_copy

class ArchiveReconcileTests(unittest.TestCase):
    def client(self):
        c=FakeIMAP((1,),capabilities=('UIDPLUS',))
        raw=b'From: synthetic@example.com\r\nMessage-ID: <1@test>\r\n\r\nSynthetic body\r\n'
        c.boxes['INBOX'][1]={b'FLAGS':(b'\\Seen',b'custom'),b'RFC822.SIZE':len(raw),b'BODY[]<0>':raw}
        c.boxes['Retained'][10001]=copy.deepcopy(c.boxes['INBOX'][1])
        return c

    def run_completion(self,c):
        return complete_archive_copy(c,'INBOX',71,1,'Retained',72,10001,'<1@test>',{})

    def assert_no_writes(self,c):
        self.assertFalse(any(x[0] in ('copy','move','delete','uid_expunge','add_flags') for x in c.calls))

    def test_exact_existing_copy_completed_without_copy_or_target_modification(self):
        c=self.client();target=copy.deepcopy(c.boxes['Retained'])
        result=self.run_completion(c)
        self.assertEqual(result['status'],'completed_existing_archive_copy')
        self.assertNotIn(1,c.boxes['INBOX']);self.assertEqual(c.boxes['Retained'],target)
        self.assertEqual([x for x in c.calls if x[0]=='uid_expunge'],[('uid_expunge',[1])])
        self.assertFalse(any(x[0] in ('copy','move') for x in c.calls))
        with self.assertRaises(core.SafetyError):self.run_completion(c)
        self.assertEqual(len([x for x in c.calls if x[0]=='uid_expunge']),1)

    def test_missing_target_or_body_flag_uidvalidity_drift_no_source_removal(self):
        for mode in ('missing','body','flags','validity','uidplus'):
            c=self.client()
            if mode=='missing':c.boxes['Retained'].clear()
            elif mode=='body':c.boxes['Retained'][10001][b'BODY[]<0>']=b'x'*c.boxes['Retained'][10001][b'RFC822.SIZE']
            elif mode=='flags':c.boxes['Retained'][10001][b'FLAGS']=(b'\\Flagged',)
            elif mode=='validity':c.validity['Retained']=99
            elif mode=='uidplus':c.capabilities.clear()
            with self.assertRaises(core.SafetyError):self.run_completion(c)
            self.assertIn(1,c.boxes['INBOX']);self.assert_no_writes(c)

    def test_truncated_or_wrong_message_identity_no_write(self):
        for mode in ('size','identity'):
            c=self.client()
            if mode=='size':c.boxes['INBOX'][1][b'RFC822.SIZE']=300000
            else:c.boxes['INBOX'][1][b'BODY[]<0>']=c.boxes['INBOX'][1][b'BODY[]<0>'].replace(b'<1@test>',b'<2@test>')
            with self.assertRaises(core.SafetyError):self.run_completion(c)
            self.assert_no_writes(c)
