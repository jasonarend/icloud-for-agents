from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, contact_content, event_content

VCARD='BEGIN:VCARD\r\nVERSION:3.0\r\nUID:keep-me\r\nFN:Before\r\nX-CUSTOM:retained\r\nEND:VCARD\r\n'
EVENT='BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:keep-me\r\nDTSTART:20261021T100000Z\r\nDTEND:20261021T110000Z\r\nSUMMARY:Before\r\nBEGIN:VALARM\r\nSUMMARY:Alarm retained\r\nACTION:DISPLAY\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'


class DAVChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=PlanStore(Path(self.tmp.name)/'plans')
        self.collection='https://p01-contacts.icloud.com/book/'
        self.resource=self.collection+'old.vcf';self.etag='"one"'
        self.transport=Mock(side_effect=lambda *args:(200,{'etag':self.etag},VCARD.encode()))
        self.reader=DAVReader('synthetic@example.com','fake-only',self.transport)
        self.reader.known_collection=lambda kind,url:url
        self.writer=Mock(side_effect=lambda kind,item:{'url':item['url'],'operation':item['operation'],'status':'applied'})
        self.editor=DAVChanges(self.reader,self.store,self.writer)

    def approve(self,proposal):
        self.store.write_once(self.store.path(proposal['plan_id'],'.approved'),
                              {'plan_id':proposal['plan_id'],'sha256':proposal['sha256']})

    def test_save_contact_prepares_without_writing_then_requires_receipt(self):
        p=self.editor.prepare('card',self.collection,[{'operation':'create','fields':{'full_name':'New Person','emails':['new@example.com']}}])
        self.writer.assert_not_called();self.transport.assert_not_called()
        with self.assertRaises(FileNotFoundError):self.editor.execute(p['plan_id'])
        self.writer.assert_not_called()
        self.approve(p);result=self.editor.execute(p['plan_id'])
        self.assertEqual(result['status'],'applied');self.assertEqual(self.writer.call_count,1)

    def test_one_approval_covers_exact_contact_batch_and_no_replay(self):
        p=self.editor.prepare('card',self.collection,[{'operation':'create','fields':{'full_name':'A'}},{'operation':'create','fields':{'full_name':'B'}}])
        self.approve(p);self.assertEqual(len(self.editor.execute(p['plan_id'])['completed']),2)
        with self.assertRaises(SafetyError):self.editor.execute(p['plan_id'])
        self.assertEqual(self.writer.call_count,2)

    def test_update_retains_uid_unknown_fields_and_escapes_injection(self):
        value=contact_content({'full_name':'After\nEMAIL:injected@example.com'},VCARD)
        self.assertIn('UID:keep-me',value);self.assertIn('X-CUSTOM:retained',value)
        self.assertIn('FN:After\\nEMAIL:injected@example.com',value)
        self.assertNotIn('\r\nEMAIL:injected@example.com',value)

    def test_etag_change_stops_before_first_write(self):
        p=self.editor.prepare('card',self.collection,[{'operation':'update','url':self.resource,'fields':{'full_name':'After'}}])
        self.approve(p);self.etag='"two"'
        self.assertEqual(self.editor.execute(p['plan_id'])['status'],'failed_or_uncertain')
        self.writer.assert_not_called()

    def test_irreversible_delete_requires_explicit_ack_and_snapshot(self):
        change={'operation':'delete','url':self.resource}
        with self.assertRaises(SafetyError):self.editor.prepare('card',self.collection,[change])
        change['irreversible_confirmed']=True
        p=self.editor.prepare('card',self.collection,[change])
        item=p['review']['payload']['changes'][0]
        self.assertEqual(item['before']['content'],VCARD)
        self.assertIn('irreversible',item['deletion'])
        self.writer.assert_not_called()

    def test_external_scheduling_and_recurrence_fail_closed(self):
        for line in ('ATTENDEE:mailto:other@example.com','ORGANIZER:mailto:me@example.com','RRULE:FREQ=DAILY','RECURRENCE-ID:20261021T100000Z'):
            before=EVENT.replace('SUMMARY:Before','SUMMARY:Before\r\n'+line)
            with self.assertRaises(SafetyError):event_content({'summary':'Changed'},before)

    def test_event_update_preserves_nested_alarm_and_uid(self):
        value=event_content({'summary':'After'},EVENT)
        self.assertIn('UID:keep-me',value);self.assertIn('SUMMARY:Alarm retained',value)
        self.assertIn('SUMMARY:After',value);self.assertNotIn('SUMMARY:Before',value)

    def test_new_event_has_no_scheduling_fields(self):
        value=event_content({'summary':'Appointment','start':'2026-10-21T10:00:00Z','end':'2026-10-21T11:00:00Z'})
        self.assertIn('DTSTART:20261021T100000Z',value)
        self.assertNotIn('ATTENDEE',value);self.assertNotIn('ORGANIZER',value);self.assertNotIn('METHOD',value)
        with self.assertRaises(SafetyError):event_content({'summary':'Bad','attendees':['a@example.com']})

    def test_bounds_duplicate_resources_and_path_escape(self):
        with self.assertRaises(SafetyError):self.editor.prepare('card',self.collection,[])
        with self.assertRaises(SafetyError):self.editor.prepare('card',self.collection,[{'operation':'create','fields':{'full_name':'A'}}]*21)
        change={'operation':'update','url':self.resource,'fields':{'full_name':'A'}}
        with self.assertRaises(SafetyError):self.editor.prepare('card',self.collection,[change,change])
        for suffix in ('../x.vcf','%2e%2e/x.vcf'):
            with self.assertRaises(SafetyError):self.editor.prepare('card',self.collection,[{'operation':'update','url':self.collection+suffix,'fields':{'full_name':'A'}}])

    def test_partial_failure_blocks_replay_and_records_completed_only(self):
        p=self.editor.prepare('card',self.collection,[{'operation':'create','fields':{'full_name':'A'}},{'operation':'create','fields':{'full_name':'B'}}])
        self.approve(p);self.writer.side_effect=[{'operation':'create','status':'applied'},TimeoutError('fake-only')]
        result=self.editor.execute(p['plan_id'])
        self.assertEqual(result['status'],'failed_or_uncertain');self.assertEqual(len(result['completed']),1)
        with self.assertRaises(SafetyError):self.editor.execute(p['plan_id'])

    def test_weak_etag_is_refused_and_long_utf8_lines_are_folded(self):
        self.etag='W/"weak"'
        with self.assertRaises(SafetyError):self.editor.resource('card',self.resource)
        value=contact_content({'full_name':'é'*200})
        self.assertTrue(all(len(line.encode())<=75 for line in value.split('\r\n')))

    def test_real_writer_conditional_headers_and_no_redirect_fallback(self):
        from unittest.mock import patch
        connection=Mock();response=connection.getresponse.return_value
        response.status=204;response.read.return_value=b''
        item={'operation':'update','url':self.resource,'before':{'etag':'"one"'},'content':VCARD}
        with patch('icloud_for_agents.dav_changes.http.client.HTTPSConnection',return_value=connection):
            self.editor._write('card',item)
            self.assertEqual(connection.request.call_args.kwargs['headers']['If-Match'],'"one"')
            item['operation']='create';item['before']=None
            self.editor._write('card',item)
            headers=connection.request.call_args.kwargs['headers']
            self.assertEqual(headers['If-None-Match'],'*');self.assertNotIn('If-Match',headers)
            response.status=302
            with self.assertRaises(SafetyError):self.editor._write('card',item)
            self.assertEqual(connection.request.call_count,3)

    def test_irreversible_delete_requires_distinct_human_phrase(self):
        from unittest.mock import patch
        from icloud_for_agents.approvals import approve_interactively
        proposal=self.editor.prepare('card',self.collection,[{'operation':'delete','url':self.resource,'irreversible_confirmed':True}])
        tty=Mock();tty.__enter__=Mock(return_value=tty);tty.__exit__=Mock(return_value=False)
        tty.readline.return_value='APPROVE CONTACTS '+proposal['plan_id']+'\n'
        with patch('builtins.open',return_value=tty):
            with self.assertRaises(SafetyError):approve_interactively(self.store,proposal['plan_id'])
        self.assertFalse(self.store.path(proposal['plan_id'],'.approved').exists())

    def test_explicit_test_event_fields_private_free_url_and_named_zone(self):
        value=event_content({'summary':'SYNTHETIC TEST','start':'2026-10-22T14:00:00-05:00','end':'2026-10-22T14:10:00-05:00','timezone':'America/Chicago','url':'https://example.com/test','free':True,'private':True,'location':'Fictional test room','description':'No real attendees'})
        self.assertIn('DTSTART:20261022T190000Z',value)
        self.assertIn('X-ICLOUD-MCP-TIMEZONE:America/Chicago',value)
        self.assertIn('URL:https://example.com/test',value)
        self.assertIn('TRANSP:TRANSPARENT',value);self.assertIn('CLASS:PRIVATE',value)
        for unsupported in ('VALARM','ATTENDEE','ORGANIZER','METHOD'):self.assertNotIn(unsupported,value)

    def test_event_invalid_zone_offset_url_or_boolean_refused(self):
        base={'summary':'test','start':'2026-10-22T14:00:00-05:00','end':'2026-10-22T14:10:00-05:00'}
        for extra in ({'timezone':'invalid/unknown'},{'timezone':'Europe/London'},{'url':'http://example.com'},{'url':'https://user:secret@example.com'},{'url':'https://example.com/\nATTENDEE:bad'},{'free':'yes'},{'private':1}):
            with self.assertRaises(SafetyError):event_content(dict(base,**extra))

    def test_contact_synthetic_edit_preserves_identity_and_untouched_fields(self):
        first=contact_content({'full_name':'Synthetic Test Contact','emails':['synthetic@example.com'],'phones':['+1-202-555-0142'],'organization':'Example Phase 1','note':'Create test'})
        changed=contact_content({'organization':'Example Phase 2','phones':['+1-202-555-0143'],'note':'Edited test'},first)
        from icloud_for_agents.dav_changes import unfold
        old_uid=[x for x in unfold(first) if x.startswith('UID:')]
        new_uid=[x for x in unfold(changed) if x.startswith('UID:')]
        self.assertEqual(old_uid,new_uid)
        self.assertIn('FN:Synthetic Test Contact',changed);self.assertIn('EMAIL:synthetic@example.com',changed)
        self.assertIn('ORG:Example Phase 2',changed);self.assertIn('TEL:+1-202-555-0143',changed)
        self.assertNotIn('TEL:+1-202-555-0142',changed);self.assertIn('NOTE:Edited test',changed)

    def test_safe_diagnostics_do_not_expose_raw_exception(self):
        from icloud_for_agents.dav_changes import DAVWriteRefused
        p=self.editor.prepare('card',self.collection,[{'operation':'create','fields':{'full_name':'Synthetic'}}])
        self.approve(p);self.writer.side_effect=DAVWriteRefused(403)
        result=self.editor.execute(p['plan_id'])
        self.assertEqual(result['diagnostic'],{'phase':'conditional_write','category':'http_refused','http_status':403})
        self.assertEqual(result['status'],'failed_or_uncertain')
        with self.assertRaises(SafetyError):self.editor.execute(p['plan_id'])

    def test_schedule_reply_header_only_on_calendar_delete_not_put(self):
        from unittest.mock import patch
        connection=Mock();connection.getresponse.return_value.status=201
        item={'operation':'create','url':'https://p01-caldav.icloud.com/cal/test.ics','before':None,'content':event_content({'summary':'Synthetic','start':'2026-10-22T19:00:00Z','end':'2026-10-22T19:10:00Z'})}
        with patch('icloud_for_agents.dav_changes.http.client.HTTPSConnection',return_value=connection):
            self.editor._write('cal',item)
            self.assertNotIn('Schedule-Reply',connection.request.call_args.kwargs['headers'])
            item.update(operation='delete',before={'etag':'"strong"'},content=None)
            self.editor._write('cal',item)
            self.assertEqual(connection.request.call_args.kwargs['headers']['Schedule-Reply'],'F')
