import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from icloud_for_agents.cleanup_access import CleanupAccess,validate
from icloud_for_agents.core import SafetyError
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP

class StandingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000.;self.root=Path(self.temp.name)
        self.scope={'grant_id':'1'*32,'account':'synthetic@example.com','operations':['archive','mark_read','trash'],
                    'source_folders':['INBOX'],'destinations':{'archive':'Retained','trash':'Bin'},
                    'max_batch':2,'max_24h':3,'issued':1000.,'expires':1000.+86400,'rules_reference':'Synthetic owner-reviewed task rules'}
        self.access=CleanupAccess(self.scope['grant_id'],self.scope['account'],root=self.root,clock=lambda:self.now)
        self.access.authority.write_once(self.access.authority.path(self.scope['grant_id'],'.json'),self.scope)
    def snapshot(self,uids):return [{'uid':n,'flags':[]} for n in uids]
    def perform(self,uids,operation='archive',action=None):
        dest=None if operation=='mark_read' else self.scope['destinations'].get(operation)
        return self.access.run(operation,'INBOX',71,uids,dest,self.snapshot(uids),action or (lambda:{'status':'moved'}))
    def test_only_approved_account_ops_folders_destinations_and_batch(self):
        for args in [('send','INBOX',[1],None),('calendar_change','INBOX',[1],None),('archive','Other',[1],'Retained'),('archive','INBOX',[1],'Other'),('archive','INBOX',[1,2,3],'Retained')]:
            with self.assertRaises(SafetyError):self.access.check(*args)
        other=CleanupAccess(self.scope['grant_id'],'other@example.com',root=self.root,clock=lambda:self.now)
        with self.assertRaises(SafetyError):other.scope()
    def test_expiry_revocation_and_scope_lifetime(self):
        self.now=self.scope['expires']
        with self.assertRaises(SafetyError):self.access.scope()
        self.now=1000.;self.access.authority.write_once(self.access.authority.path(self.scope['grant_id'],'.revoked'),{'revoked':1000})
        with self.assertRaises(SafetyError):self.access.scope()
        d=dict(self.scope,expires=999)
        with self.assertRaises(SafetyError):validate(d,d['account'],1000)
    def test_quota_counts_distinct_messages_not_read_plus_archive(self):
        self.perform([1,2],'mark_read');self.perform([1,2]);self.perform([3])
        action=Mock()
        with self.assertRaises(SafetyError):self.perform([4],action=action)
        action.assert_not_called()
    def test_rolling_quota_expires_but_same_action_is_never_replayed(self):
        self.perform([1,2]);self.perform([3]);self.now+=86401
        # New grant with later expiration, same account: historical quota ages out.
        updated=dict(self.scope,grant_id='2'*32,issued=self.now,expires=self.now+86400)
        self.access.authority.write_once(self.access.authority.path('2'*32,'.json'),updated)
        self.access.grant_id='2'*32;self.perform([4])
        with self.assertRaises(SafetyError):self.perform([4])
    def test_replay_and_uncertain_outcome_block_new_mutations(self):
        self.perform([1]);action=Mock()
        with self.assertRaises(SafetyError):self.perform([1],action=action)
        action.assert_not_called()
        failing=Mock(side_effect=TimeoutError('SYNTHETIC_SENSITIVE_DETAILS'))
        with self.assertRaises(SafetyError) as caught:self.perform([2],action=failing)
        self.assertNotIn('SYNTHETIC_SENSITIVE',str(caught.exception));self.assertEqual(failing.call_count,1)
        with self.assertRaises(SafetyError):self.perform([3],action=action)
        action.assert_not_called()
    def test_stars_and_invalid_identity_never_call_action(self):
        action=Mock()
        for items,validity in (([{'uid':1,'flags':['\\Flagged']}],71),([{'uid':2,'flags':[]}],71),(self.snapshot([1]),True)):
            with self.assertRaises(SafetyError):self.access.run('archive','INBOX',validity,[1],'Retained',items,action)
        action.assert_not_called()
    def service(self):
        c=FakeIMAP((1,2),capabilities=('UIDPLUS',))
        for row in c.boxes['INBOX'].values():row[b'FLAGS']=()
        store=PlanStore(self.root/'mail-plans',clock=lambda:self.now)
        return c,MailService(self.scope['account'],'fake',connector=lambda:c,store=store)
    def test_standing_trash_consumes_exact_plan_without_daily_receipt(self):
        c,service=self.service()
        with patch('icloud_for_agents.service.cleanup_access',return_value=self.access):
            p=service.prepare_trash('INBOX',71,[1],'Synthetic reviewed junk')
            result=service.execute(p['plan_id'])
            self.assertEqual(result['status'],'moved');self.assertNotIn(1,c.boxes['INBOX'])
            self.assertIn('cleanup_authorization',result)
            with self.assertRaises(SafetyError):service.execute(p['plan_id'])
        self.assertFalse(service.store.path(p['plan_id'],'.approved').exists())
    def test_without_grant_trash_still_needs_human_receipt(self):
        c,service=self.service()
        with patch('icloud_for_agents.service.cleanup_access',return_value=None):
            p=service.prepare_trash('INBOX',71,[1],'Synthetic reviewed junk')
            with self.assertRaises(FileNotFoundError):service.execute(p['plan_id'])
        self.assertIn(1,c.boxes['INBOX'])
    def test_standing_read_then_archive_preserves_flags_and_excludes_restore(self):
        c,service=self.service()
        with patch('icloud_for_agents.service.cleanup_access',return_value=self.access):
            service.set_seen(folder='INBOX',uidvalidity=71,uids=[1],seen=True)
            result=service.archive('INBOX',71,[1]);self.assertEqual(result['status'],'moved')
            self.assertEqual(c.boxes['Retained'][10001][b'FLAGS'],(b'\\Seen',))
            with self.assertRaises(SafetyError):service.set_seen(folder='INBOX',uidvalidity=71,uids=[2],seen=False)
            with self.assertRaises(SafetyError):service.restore(73,[1],'INBOX')
    def test_send_still_needs_separate_receipt(self):
        c,service=self.service()
        p=service.store.prepare('send',{'account':self.scope['account']})
        with patch('icloud_for_agents.service.cleanup_access',return_value=self.access),patch('icloud_for_agents.service.smtplib.SMTP') as smtp:
            with self.assertRaises(FileNotFoundError):service.execute(p['plan_id'])
            smtp.assert_not_called()

    def test_owner_activation_decline_creates_no_grant_or_profile_change(self):
        import json
        from icloud_for_agents import cleanup_access as module
        profile=self.root/'project/local';profile.mkdir(parents=True)
        settings={'account':self.scope['account']};(profile/'settings.json').write_text(json.dumps(settings))
        request={k:v for k,v in self.scope.items() if k not in {'grant_id','issued','expires'}};request['days']=1
        file=self.root/'request.json';file.write_text(json.dumps(request));tty=Mock();tty.__enter__=Mock(return_value=tty);tty.__exit__=Mock(return_value=False);tty.readline.return_value='yes\n'
        with patch.object(module,'__file__',str(self.root/'project/src/icloud_for_agents/cleanup_access.py')),patch('icloud_for_agents.local_config.read_settings',return_value=settings),patch('sys.argv',['command','--review-activate',str(file)]),patch('builtins.open',return_value=tty),patch.object(module,'runtime_root',return_value=self.root/'owner-runtime'):
            with self.assertRaises(SafetyError):module.main()
        self.assertEqual(json.loads((profile/'settings.json').read_text()),settings)
        self.assertFalse((self.root/'owner-runtime/cleanup-authority').exists())

class OptionalCleanupLimitsTests(StandingCleanupTests):
    def test_no_aggregate_cap_or_expiry_still_bounds_batches_and_scope(self):
        scope=dict(self.scope,max_24h=None,expires=None)
        validate(scope,self.scope['account'],1000+365*86400)
        self.access.authority.path(self.scope['grant_id'],'.json').unlink()
        self.access.authority.write_once(self.access.authority.path(self.scope['grant_id'],'.json'),scope)
        for n in range(1,7):self.perform([n])
        with self.assertRaises(SafetyError):self.perform([20,21,22])
