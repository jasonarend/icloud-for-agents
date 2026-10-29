"""Synthetic malformed native format and UTF-16 checklist regression cases."""
import gzip,hashlib,unittest,uuid
from icloud_for_agents.core import SafetyError
from icloud_for_agents.notes_checklist_format import decode,MAX_DECOMPRESSED,MAX_ITEMS

def varint(value):
 out=bytearray()
 while value>127:out.append((value&127)|128);value>>=7
 out.append(value);return bytes(out)
def field(number,value,wire=None):
 wire=(0 if type(value)is int else 2)if wire is None else wire
 return varint(number*8+wire)+(varint(value)if wire==0 else varint(len(value))+value)
def style(identity=1,done=0,indent=0):return field(1,103)+field(4,indent)+field(5,field(1,uuid.UUID(int=identity).bytes)+field(2,done))
def run(text,paragraph=None):return field(1,len(text.encode('utf-16-le'))//2)+(field(2,paragraph)if paragraph is not None else b'')
def raw(text,runs,version=0):return field(2,field(2,version)+field(3,field(2,text.encode())+b''.join(field(5,r)for r in runs)))
def blob(text,runs,version=0):return gzip.compress(raw(text,runs,version),mtime=0)
def fixture():
 lines=['Title\n','First 🚀\n','Second\n','Nested\n','- [x] Plain text\n'];styles=[None,style(1),style(2,1),style(3,0,1),None]
 return ''.join(lines),[run(line,p)for line,p in zip(lines,styles)]
class ChecklistFormatTests(unittest.TestCase):
 def test_ids_checked_indent_order_and_utf16_positions_preserve_emoji(self):
  text,runs=fixture();value=decode(blob(text,runs));self.assertEqual([(v['text'],v['checked'],v['indent'])for v in value['items']],[('First 🚀',False,0),('Second',True,0),('Nested',False,1)]);self.assertEqual(value['items'][1]['start_utf16'],15);self.assertEqual(value['items'][0]['id'],str(uuid.UUID(int=1)));self.assertEqual(value['text_sha256'],hashlib.sha256(text.encode()).hexdigest());self.assertFalse(value['representation_complete'])
 def test_plain_checklist_glyphs_are_never_native_items_and_empty_text_is_valid(self):
  text='☑ - [x] Plain\n';self.assertEqual(decode(blob(text,[run(text)]))['items'],[]);self.assertEqual(decode(blob('',[]))['items'],[])
 def test_inline_style_splits_can_share_one_native_item_identity(self):
  text='First bold 🚀\n';runs=[run('First ',style(1)),run('bold',style(1))+field(5,1),run(' 🚀\n',style(1))];self.assertEqual(decode(blob(text,runs))['items'][0]['text'],'First bold 🚀')
 def test_missing_mixed_or_disagreeing_paragraph_state_refuses(self):
  for runs in ([run('A',style(1)),run('\n')],[run('A',style(1)),run('\n',style(2))],[run('A',style(1)),run('\n',style(1,1))]):
   with self.subTest(runs=runs),self.assertRaises(SafetyError):decode(blob('A\n',runs))
 def test_repeated_item_id_across_paragraphs_refuses(self):
  with self.assertRaises(SafetyError):decode(blob('A\nB\n',[run('A\n',style(1)),run('B\n',style(1))]))
 def test_runs_must_cover_text_exactly_without_splitting_a_surrogate_pair(self):
  for text,runs in [('A',[]),('A',[field(1,0)]),('A',[field(1,2)]),('AB',[field(1,1)]),('🚀',[field(1,1),field(1,1)])]:
   with self.subTest(text=text,runs=runs),self.assertRaises(SafetyError):decode(blob(text,runs))
 def test_missing_uuid_checked_unknown_state_and_indent_refuse(self):
  for p in [field(1,103),field(1,103)+field(5,field(2,0)),field(1,103)+field(5,field(1,b'x')+field(2,0)),style(1,2),style(1,0,33),field(1,103)+field(5,field(1,uuid.UUID(int=1).bytes)),field(1,1)+field(5,field(1,uuid.UUID(int=1).bytes)+field(2,0))]:
   with self.subTest(p=p),self.assertRaises(SafetyError):decode(blob('A\n',[run('A\n',p)]))
 def test_duplicate_singular_fields_and_wrong_wire_types_refuse(self):
  for data in [raw('A',[run('A')])+raw('A',[run('A')]),field(2,1),field(2,field(2,0)+field(3,field(2,b'A')+field(2,b'B'))),raw('A',[field(1,1)+field(1,1)])]:
   with self.subTest(data=data),self.assertRaises(SafetyError):decode(gzip.compress(data))
 def test_bad_varints_field_ids_lengths_groups_utf8_and_unknown_version_refuse(self):
  for data in [b'\x80',b'\x80'*10+b'\x00',b'\x80\x00',b'\x00\x00',b'\x13',b'\x12\x7f',field(2,field(2,0)+field(3,field(2,b'\xff'))),raw('A',[run('A')],1)]:
   with self.subTest(data=data),self.assertRaises(SafetyError):decode(gzip.compress(data))
 def test_gzip_corruption_trailing_member_and_decompression_bound_refuse(self):
  text,runs=fixture();good=blob(text,runs)
  for value in (b'not gzip',good[:-1],good+b'junk',good+good,gzip.compress(b'a'*(MAX_DECOMPRESSED+1))):
   with self.subTest(length=len(value)),self.assertRaises(SafetyError):decode(value)
 def test_field_item_text_and_run_budgets_refuse_without_silent_truncation(self):
  cases=[gzip.compress(field(1,0)*50001),blob('x'*100001,[run('x'*100001)]),blob('\n'*10001,[run('\n')]*10001),blob('A\n'*(MAX_ITEMS+1),[run('A\n',style(i+1))for i in range(MAX_ITEMS+1)])]
  for value in cases:
   with self.subTest(length=len(value)),self.assertRaises(SafetyError):decode(value)
 def test_soft_line_and_other_unknown_paragraph_breaks_refuse(self):
  for separator in ('\r','\v','\f','\x1c','\x1d','\x1e','\x85','\u2028','\u2029'):
   text='A'+separator+'B'
   with self.subTest(separator=separator),self.assertRaises(SafetyError):decode(blob(text,[run(text,style(1))]))
 def test_ascii_record_separators_cannot_invent_a_checklist_paragraph(self):
  for separator in ('\x1c','\x1d','\x1e'):
   prefix='Heading'+separator;text=prefix+'Item\n'
   with self.subTest(separator=separator),self.assertRaises(SafetyError):decode(blob(text,[run(prefix),run('Item\n',style(1))]))
