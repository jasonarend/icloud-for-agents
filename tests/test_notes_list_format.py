"""Bounded Markdown list conversion is explicit and never creates native checklists."""
import unittest
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_notes import prepare_content
from icloud_for_agents.notes_list_format import format_lists

class NotesListFormatTests(unittest.TestCase):
    def test_headings_paragraphs_and_nested_mixed_lists(self):
        value=format_lists('# Plan\nOverview\n- First\n  1. One\n  2. Two\n- Second\n\n1. Next\n2. Last')
        self.assertEqual(value['body'],'<h1>Plan</h1><p>Overview</p><ul><li>First<ol><li>One</li><li>Two</li></ol></li><li>Second</li></ul><ol><li>Next</li><li>Last</li></ol>')
        self.assertFalse(value['coverage']['native_checklists']);self.assertFalse(value['coverage']['round_trip_safe'])
        prepared=prepare_content({'title':'Synthetic','body':value['body'],'format':'html'})
        self.assertEqual(prepared['html'],value['body']);self.assertIn('First',prepared['expected_text'])
    def test_numbering_nesting_and_unsupported_syntax_refused(self):
        for text in ('2. Starts late','1. One\n3. Skips','  - Orphan','- One\n    - Jump',
                     '- [ ] Checklist','**bold**','[link](https://example.com)','```code```',
                     '<script>alert(1)</script>','- One\n\t- Child','##Heading','- One\n  prose','- # Heading','- 1. Nested'):
            with self.subTest(text=text),self.assertRaises(SafetyError):format_lists(text)
    def test_html_is_escaped_and_bounded(self):
        value=format_lists('Text & more\n- A & B')
        self.assertIn('Text &amp; more',value['body']);self.assertIn('A &amp; B',value['body'])
        for value in ('','\n\n','x'*80001,'- x\n'*1001,'bad\x00text','bad\x7ftext'):
            with self.subTest(length=len(value)),self.assertRaises(SafetyError):format_lists(value)
    def test_list_type_change_does_not_merge_or_reorder_items(self):
        result=format_lists('- First\n1. Second\n  - Child\n2. Third')
        self.assertEqual(result['body'],'<ul><li>First</li></ul><ol><li>Second<ul><li>Child</li></ul></li><li>Third</li></ol>')
        self.assertEqual(format_lists('+ Plus\n* Star')['body'],'<ul><li>Plus</li><li>Star</li></ul>')
if __name__=='__main__':unittest.main()
