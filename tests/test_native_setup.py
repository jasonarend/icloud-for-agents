"""Reviewed setup edits with synthetic settings and fixed local helper fixtures."""
import copy,json,os,plistlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from icloud_for_agents.core import SafetyError
from icloud_for_agents.local_config import read_settings,child_environment
from icloud_for_agents.native_setup import preview,apply
from icloud_for_agents.approvals import PlanStore

class NativeSetupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name);(self.root/'local').mkdir(mode=0o700)
        self.settings={'account':'synthetic@example.com','apple_keychain_service':'synthetic-credential-label','access_mode':'write-enabled','dav_reads':True,'approval_policy':'local-receipt','runtime_directory':str(self.root/'runtime')}
        self.path=self.root/'local/settings.json';self.save()
        self.bundle=self.root/'Helper.app';self.helper=self.bundle/'Contents/MacOS/icloud-agents-native';self.helper.parent.mkdir(parents=True);self.helper.write_text('synthetic executable');self.helper.chmod(0o700)
        (self.bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':self.helper.name,'CFBundleIdentifier':'com.jasonarend.icloud-for-agents.native','CFBundlePackageType':'APPL'}));resources=self.bundle/'Contents/Resources';resources.mkdir()
        for name in ('notes.js','notes-write.js'):(resources/name).write_text('synthetic fixed script')
        p=patch('icloud_for_agents.native_setup.verify_signature',return_value=None);self.signature=p.start();self.addCleanup(p.stop)
    def save(self):self.path.write_text(json.dumps(self.settings));self.path.chmod(0o600)
    def proposal(self,**kw):return preview(self.root,'notes',self.helper,**kw)[0]
    def test_preview_does_not_write_or_launch_and_new_service_is_readonly_discovery(self):
        before=self.path.read_bytes();files=set(self.root.rglob('*'));plan=self.proposal();self.assertEqual(self.path.read_bytes(),before);self.assertEqual(set(self.root.rglob('*')),files)
        self.assertTrue(plan['review']['effects']['source_discovery_only']);self.assertFalse(plan['review']['effects']['source_ids_verified']);self.assertEqual(plan['review']['after']['effective_access_after_restart'],'read-only');self.assertNotIn('synthetic-credential-label',json.dumps(plan))
    def test_apply_preserves_legacy_services_policy_secrets_labels_and_global_mode(self):
        original=self.path.read_bytes();plan=self.proposal();result=apply(self.root,'notes',self.helper,plan['sha256']);updated=read_settings(self.root)
        self.assertEqual(Path(result['backup_path']).read_bytes(),original);self.assertEqual(Path(result['backup_path']).stat().st_mode&0o777,0o600);self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        for key in self.settings:self.assertEqual(updated[key],self.settings[key])
        self.assertEqual(updated['services'],{'mail':True,'calendar':True,'contacts':True,'notes':True,'reminders':False});self.assertEqual(updated['native_services']['notes']['access_mode'],'read-only');self.assertNotIn('folder_write_backend',updated['native_services']['notes']);self.assertFalse(result['os_permissions_changed']);self.assertTrue(result['server_restart_required'])
        self.assertEqual(child_environment(updated,{}).get('ICLOUD_ENABLE_NOTES'),'1')
        with self.assertRaises(SafetyError):apply(self.root,'notes',self.helper,plan['sha256'])
    def test_exact_source_selection_and_clear_preserve_other_native_app(self):
        self.settings.update(services={'mail':False,'calendar':True,'contacts':False,'notes':True,'reminders':True},native_services={'notes':{'source_ids':['old'],'access_mode':'write-enabled','helper_path':str(self.helper)},'reminders':{'source_ids':['reminder-source'],'access_mode':'read-only','helper_path':str(self.helper)}});self.save()
        plan=self.proposal(source_ids=['selected']);apply(self.root,'notes',self.helper,plan['sha256'],source_ids=['selected']);result=read_settings(self.root);self.assertEqual(result['native_services']['reminders'],self.settings['native_services']['reminders']);self.assertEqual(result['native_services']['notes']['access_mode'],'write-enabled')
        plan=self.proposal(source_ids=[]);apply(self.root,'notes',self.helper,plan['sha256'],source_ids=[]);self.assertEqual(read_settings(self.root)['native_services']['notes']['source_ids'],[])
    def test_newly_reenabled_app_cannot_resurrect_latent_write_mode(self):
        self.settings.update(services={'mail':True,'calendar':False,'contacts':False,'notes':False},native_services={'notes':{'source_ids':['selected'],'access_mode':'write-enabled'}});self.save();self.assertEqual(self.proposal()['review']['after']['configuration']['access_mode'],'read-only')
    def test_stale_settings_scope_policy_or_helper_refuse_without_edit(self):
        plan=self.proposal();self.settings['approval_policy']='trusted-client';self.save();current=self.path.read_bytes()
        with self.assertRaises(SafetyError):apply(self.root,'notes',self.helper,plan['sha256'])
        self.assertEqual(self.path.read_bytes(),current);self.settings['approval_policy']='local-receipt';self.save();self.helper.write_text('changed synthetic executable')
        with self.assertRaises(SafetyError):apply(self.root,'notes',self.helper,plan['sha256'])
        self.assertEqual(list((self.root/'local').glob('settings.before-native-*')),[])
    def test_review_is_bound_to_the_exact_installation(self):
        plan=self.proposal();other=self.root/'other-install';other.mkdir();(other/'local').mkdir(mode=0o700);path=other/'local/settings.json';path.write_bytes(self.path.read_bytes());path.chmod(0o600)
        with self.assertRaises(SafetyError):apply(other,'notes',self.helper,plan['sha256'])
        self.assertEqual(path.read_bytes(),self.path.read_bytes())

    def test_invalid_signature_source_schema_and_symlinks_refuse(self):
        self.signature.side_effect=SafetyError('Invalid signature')
        with self.assertRaises(SafetyError):self.proposal()
        self.signature.side_effect=None
        for sources in (['*'],['a','a'],[True],['x\n']):
            with self.subTest(sources=sources),self.assertRaises((SafetyError,ValueError)):self.proposal(source_ids=sources)
        self.path.unlink();other=self.root/'other.json';other.write_text('{}');self.path.symlink_to(other)
        with self.assertRaises(OSError):self.proposal()
    def test_unfinished_write_and_active_execution_lock_block_apply(self):
        from icloud_for_agents.native_changes import NativeChanges
        plan=self.proposal();store=PlanStore(self.root/'runtime');old=store.prepare('notes_change',{'account':self.settings['account'],'changes':[]});store.write_once(store.path(old['plan_id'],'.started'),{'synthetic':'unfinished'})
        with self.assertRaises(SafetyError):apply(self.root,'notes',self.helper,plan['sha256'])
        store.path(old['plan_id'],'.started').unlink()
        with NativeChanges('notes',self.settings['account'],None,store).serialized():
            with self.assertRaises(SafetyError):apply(self.root,'notes',self.helper,plan['sha256'])
        self.assertNotIn('native_services',read_settings(self.root))
    def test_disable_needs_no_helper_and_preserves_its_configuration(self):
        self.settings.update(services={'mail':True,'calendar':False,'contacts':False,'notes':True},native_services={'notes':{'source_ids':['selected'],'access_mode':'write-enabled','helper_path':'/missing/helper'}});self.save();plan=preview(self.root,'notes',enabled=False)[0];result=apply(self.root,'notes',None,plan['sha256'],enabled=False)
        updated=read_settings(self.root);self.assertFalse(updated['services']['notes']);self.assertEqual(updated['native_services'],self.settings['native_services']);self.assertEqual(result['effective_access_after_restart'],'disabled');self.signature.assert_not_called()

    def test_optional_reminders_store_selection_is_review_bound_reversible_and_does_not_enable_writes(self):
        self.settings.update(services={'mail':False,'calendar':False,'contacts':False,'notes':False,'reminders':True},native_services={
            'reminders':{'source_ids':['selected'],'access_mode':'read-only','helper_path':str(self.helper),'advanced_backend':'reminderkit'}});self.save()
        original=self.path.read_bytes();plan=preview(self.root,'reminders',self.helper,metadata_backend='reminders-store-readonly')[0]
        self.assertEqual(self.path.read_bytes(),original);self.assertEqual(plan['review']['after']['effective_access_after_restart'],'read-only')
        with self.assertRaises(SafetyError):apply(self.root,'reminders',self.helper,plan['sha256'],metadata_backend='native')
        apply(self.root,'reminders',self.helper,plan['sha256'],metadata_backend='reminders-store-readonly');updated=read_settings(self.root)
        self.assertEqual(updated['native_services']['reminders']['metadata_backend'],'reminders-store-readonly');self.assertEqual(updated['native_services']['reminders']['source_ids'],['selected']);self.assertEqual(updated['approval_policy'],'local-receipt')
        off=preview(self.root,'reminders',self.helper,metadata_backend='native')[0];apply(self.root,'reminders',self.helper,off['sha256'],metadata_backend='native')
        self.assertEqual(read_settings(self.root)['native_services']['reminders']['metadata_backend'],'native')
        with self.assertRaises(ValueError):preview(self.root,'reminders',self.helper,metadata_backend='notes-store-readonly')
        with self.assertRaises(SafetyError):preview(self.root,'reminders',enabled=False,metadata_backend='native')
