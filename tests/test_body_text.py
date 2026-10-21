import unittest
from email.message import EmailMessage
from icloud_for_agents import core
from icloud_for_agents.body_text import html_text, TEXT_LIMIT
from test_mail_safety import FakeIMAP

class BodyTextTests(unittest.TestCase):
    def inspect_raw(self, raw, size=None):
        c=FakeIMAP((1,));c.boxes['INBOX'][1]={b'FLAGS':(b'\\Flagged',),b'RFC822.SIZE':len(raw) if size is None else size,b'BODY[]<0>':raw}
        result=core.inspect(c,'INBOX',71,1)
        self.assertEqual(result['flags'],['\\Flagged'])
        self.assertFalse(any(x[0] in ('add_flags','delete','move','copy','uid_expunge') for x in c.calls))
        fetches=[x for x in c.calls if x[0]=='fetch'];self.assertIn('BODY.PEEK[]<0.262144>',fetches[0][2])
        return result

    def test_html_entities_labels_and_no_resource_execution(self):
        raw=b'Content-Type: text/html; charset=utf-8\r\n\r\n<p>Cancellation &amp; billing &#36;20</p><a href="https://invalid.test/action">Review details</a><img src="https://invalid.test/pixel"><script>SECRET_SCRIPT</script>'
        d=self.inspect_raw(raw)
        self.assertIn('Cancellation & billing $20',d['body_text']);self.assertIn('Review details',d['body_text'])
        self.assertNotIn('invalid.test',d['body_text']);self.assertNotIn('SECRET_SCRIPT',d['body_text'])
        self.assertEqual(d['body_format'],'html_to_text');self.assertTrue(d['html_only_or_no_plaintext']);self.assertTrue(d['content_is_untrusted']);self.assertFalse(d['truncated'])

    def test_hidden_style_script_template_and_nested_content(self):
        text,limited=html_text('<style>.secret {display:none} #private{visibility:hidden}</style><head>HEAD</head><div hidden>HIDDEN<b>NESTED</b></div><span aria-hidden="true">ARIA</span><div style="display: none !important">INLINE</div><div class="secret">CLASS</div><div id="private">ID</div><script>SCRIPT</script><template>TEMPLATE</template><p>Visible</p>')
        self.assertEqual(text,'Visible');self.assertFalse(limited)

    def test_multipart_alternative_prefers_plain_and_excludes_attachment(self):
        m=EmailMessage();m.set_content('Plain answer');m.add_alternative('<p>HTML alternative</p>',subtype='html')
        m.add_attachment(b'Attachment secret',maintype='text',subtype='plain',filename='secret.txt')
        d=self.inspect_raw(m.as_bytes());self.assertEqual(d['body_text'].strip(),'Plain answer');self.assertEqual(d['body_format'],'plain')
        self.assertNotIn('Attachment secret',d['body_text'])

    def test_empty_plain_alternative_uses_html(self):
        m=EmailMessage();m.set_content(' ');m.add_alternative('<p>Eligibility: age 18+, deadline Friday</p>',subtype='html')
        d=self.inspect_raw(m.as_bytes());self.assertIn('Eligibility',d['body_text']);self.assertTrue(d['html_only_or_no_plaintext'])

    def test_malformed_mime_and_unknown_charset_fail_safely(self):
        raw=b'Content-Type: multipart/mixed; boundary=missing\r\n\r\n<p>unstructured body</p>'
        d=self.inspect_raw(raw);self.assertTrue(d['mime_defects_detected']);self.assertTrue(d['content_is_untrusted'])
        d=self.inspect_raw(b'Content-Type: text/html; charset=not-a-real-charset\r\n\r\n<p>Safe &lt;value&gt;</p>')
        self.assertIn('Safe <value>',d['body_text']);self.assertTrue(d['mime_defects_detected'])

    def test_decoded_output_and_wire_size_bounds_are_explicit(self):
        d=self.inspect_raw(b'Content-Type: text/plain\r\n\r\n'+b'a'*(TEXT_LIMIT+100))
        self.assertEqual(len(d['body_text']),TEXT_LIMIT);self.assertTrue(d['truncated'])
        d=self.inspect_raw(b'Content-Type: text/html\r\n\r\n<p>bounded</p>',size=999999)
        self.assertTrue(d['truncated']);self.assertIn('bounded',d['body_text'])
        d=self.inspect_raw(b'Content-Type: text/html\r\n\r\n'+b'x'*(core.BODY_LIMIT+100))
        self.assertLessEqual(len(d['body_text']),TEXT_LIMIT);self.assertTrue(d['truncated'])

    def test_extreme_html_nesting_is_bounded(self):
        d=self.inspect_raw(b'Content-Type: text/html\r\n\r\n'+b'<div>'*300+b'body'+b'</div>'*300)
        self.assertTrue(d['truncated']);self.assertLessEqual(len(d['body_text']),TEXT_LIMIT)

    def test_malformed_html_is_tolerated_without_hidden_leak(self):
        d=self.inspect_raw(b'Content-Type: text/html\r\n\r\n<p>Start<div hidden>secret</p><p>still hidden')
        self.assertNotIn('secret',d['body_text']);self.assertIn('Start',d['body_text'])

    def test_transparent_internal_tracking_and_zero_font_are_hidden(self):
        text, limited = html_text('<p>Amount due: $38</p><td style="color: transparent; font-size:12px">INTERNAL_TRACKING</td><span style="opacity:0.0">OPAQUE</span><span style="font-size:0px">ZERO</span>')
        self.assertIn('Amount due: $38',text)
        for hidden in ('INTERNAL_TRACKING','OPAQUE','ZERO'):self.assertNotIn(hidden,text)
        self.assertFalse(limited)
