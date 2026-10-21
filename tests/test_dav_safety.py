import unittest
from unittest.mock import Mock
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader, apple_url, xml_document, register_read_tools


class DAVSafetyTests(unittest.TestCase):
    def test_exact_service_hosts_only(self):
        self.assertEqual(apple_url('https://p72-caldav.icloud.com/a/', 'cal'), 'https://p72-caldav.icloud.com/a/')
        for value in ('http://caldav.icloud.com/', 'https://caldav.icloud.com.evil.test/',
                      'https://evil.test/', 'https://user:pass@caldav.icloud.com/',
                      'https://contacts.icloud.com/', 'https://caldav.icloud.com:444/',
                      'https://caldav.icloud.com/?secret=x'):
            with self.assertRaises(SafetyError):apple_url(value, 'cal')

    def test_redirect_host_checked_before_credential_transport(self):
        transport=Mock(return_value=(302, {'location':'https://evil.test/'}, b''))
        reader=DAVReader('synthetic','fake',transport)
        with self.assertRaises(SafetyError):reader.request('cal','PROPFIND','https://caldav.icloud.com/')
        self.assertEqual(transport.call_count,1)

    def test_no_write_or_invitation_method(self):
        transport=Mock()
        reader=DAVReader('synthetic','fake',transport)
        for method in ('PUT','DELETE','POST','MKCALENDAR','PROPPATCH'):
            with self.assertRaises(SafetyError):reader.request('cal',method,'https://caldav.icloud.com/')
        transport.assert_not_called()

    def test_xml_dtd_and_entity_refused(self):
        for raw in (b'<!DOCTYPE x><x/>',b'<!ENTITY x "abc"><x/>',b'\x00<!DOCTYPE x>'):
            with self.assertRaises(SafetyError):xml_document(raw)

    def test_bad_discovery_href_not_requested(self):
        xml=b'<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:current-user-principal><d:href>https://evil.test/</d:href></d:current-user-principal></d:prop></d:propstat></d:response></d:multistatus>'
        transport=Mock(return_value=(207,{},xml))
        with self.assertRaises(SafetyError):DAVReader('a','fake',transport).collections('cal')
        self.assertEqual(transport.call_count,1)

    def test_calendar_time_bounds_before_report(self):
        reader=DAVReader('a','fake',Mock())
        reader.known_collection=lambda kind,url:url
        for start,end in (('2026-10-01','2026-10-02'),('2026-10-01T00:00:00Z','2027-01-01T00:00:00Z')):
            with self.assertRaises(SafetyError):reader.events('https://caldav.icloud.com/a/',start,end)
        reader.transport.assert_not_called()

    def test_contact_cannot_escape_addressbook(self):
        reader=DAVReader('a','fake',Mock())
        reader.known_collection=lambda kind,url:url
        with self.assertRaises(SafetyError):reader.contact('https://contacts.icloud.com/a/','https://contacts.icloud.com/b/x.vcf')
        reader.transport.assert_not_called()

    def test_only_five_read_tools_registered_without_credentials(self):
        registered=[]
        class Registry:
            def tool(self,**kwargs):
                def decorate(fn):
                    registered.append((fn.__name__,kwargs['annotations']))
                    return fn
                return decorate
        service=Mock(side_effect=AssertionError('No credentials during registration'))
        register_read_tools(Registry(),lambda fn:fn,service)
        self.assertEqual({name for name,_ in registered},{'calendar_list','calendar_events','contacts_addressbooks','contacts_list','contacts_get'})
        self.assertTrue(all(ann['readOnlyHint'] and not ann['destructiveHint'] for _,ann in registered))
        service.assert_not_called()

    def test_read_refusal_reports_only_safe_status_not_body_and_no_retry(self):
        transport=Mock(return_value=(429,{},b'SYNTHETIC_PRIVATE_CONTACT_OR_CREDENTIAL'))
        reader=DAVReader('synthetic','fake',transport)
        with self.assertRaises(SafetyError) as caught:reader.request('card','GET','https://contacts.icloud.com/a/x.vcf')
        self.assertIn('HTTP 429 during card/GET',str(caught.exception))
        self.assertNotIn('SYNTHETIC_PRIVATE',str(caught.exception))
        self.assertEqual(transport.call_count,1)
