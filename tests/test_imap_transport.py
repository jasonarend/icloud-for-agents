import io
import unittest
from unittest.mock import patch
from icloud_for_agents.imap_transport import CompatibleTLS, CompatibleIMAPClient
from imapclient.tls import IMAP4_TLS

class TransportTests(unittest.TestCase):
    def test_stream_assignment_and_read_on_modern_python(self):
        transport = object.__new__(CompatibleTLS)
        stream = io.BytesIO(b"hello\r\n")
        transport.file = stream
        self.assertIs(transport._file, stream)
        self.assertEqual(transport.readline(), b"hello\r\n")
        self.assertNotIn("file", IMAP4_TLS.__dict__)

    def test_tls_factory_preserves_context_host_and_timeout(self):
        client = object.__new__(CompatibleIMAPClient)
        client.ssl, client.stream = True, False
        client.host, client.port, client.ssl_context = "imap.mail.me.com", 993, object()
        client._timeout = type("Timeout", (), {"connect": 30})()
        with patch("icloud_for_agents.imap_transport.CompatibleTLS") as factory:
            self.assertIs(client._create_IMAP4(), factory.return_value)
            factory.assert_called_once_with(client.host, 993, client.ssl_context, 30)

class CopyUIDResponseTests(unittest.TestCase):
    def client(self):
        import imaplib
        c=object.__new__(CompatibleIMAPClient)
        c._imap=object.__new__(imaplib.IMAP4)
        c._imap.debug=0;c._imap.untagged_responses={}
        return c

    def test_real_imaplib_response_pop_captures_fresh_untagged(self):
        c=self.client();c._imap.untagged_responses={'COPYUID':[b'1 9 99']}
        def execute(*a,**k):
            c._imap.untagged_responses['COPYUID']=[b'73 1:2 10001:10002']
            return b'COPY completed'
        with patch('imapclient.IMAPClient._command_and_check',side_effect=execute) as command:
            self.assertEqual(c._command_and_check('copy',unpack=True),b'[COPYUID 73 1:2 10001:10002]')
            self.assertEqual(command.call_count,1)
        self.assertNotIn('COPYUID',c._imap.untagged_responses)

    def test_stale_mapping_cannot_be_used_and_no_write_retry(self):
        c=self.client();c._imap.untagged_responses={'COPYUID':[b'73 1 2']}
        with patch('imapclient.IMAPClient._command_and_check',return_value=b'COPY completed') as command:
            self.assertEqual(c._command_and_check('copy'),b'COPY completed')
            self.assertEqual(command.call_count,1)

    def test_conflicting_tagged_and_fresh_mapping_fails_closed(self):
        from icloud_for_agents.core import SafetyError
        c=self.client()
        def execute(*a,**k):
            c._imap.untagged_responses['COPYUID']=[b'73 1 999']
            return b'[COPYUID 73 1 10001] copied'
        with patch('imapclient.IMAPClient._command_and_check',side_effect=execute) as command:
            with self.assertRaises(SafetyError):c._command_and_check('copy')
            self.assertEqual(command.call_count,1)
