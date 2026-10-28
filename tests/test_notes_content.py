"""Native table placeholders must not cause false failures or lost search text."""
import unittest
from icloud_for_agents.notes_content import project_html


class NotesContentTests(unittest.TestCase):
    def test_native_table_export_preserves_cells_missing_from_plaintext(self):
        html='<div>Title</div><object><table><tbody><tr><td><div>Priority</div></td><td><b>High</b></td></tr></tbody></table></object>'
        result=project_html(html)
        self.assertEqual(result['text'],'Title Priority High')
        self.assertEqual(result['tables'],[[['Priority','High']]])
        self.assertTrue(result['text_projection_complete'])
    def test_normalized_header_cells_and_wrappers_keep_same_semantics(self):
        expected=project_html('<table><tr><th>A</th><th>B</th></tr></table>')
        actual=project_html('<object><table><tbody><tr><td><div><b>A</b></div></td><td><div>B</div></td></tr></tbody></table></object>')
        self.assertEqual(expected['tables'],actual['tables'])
        self.assertEqual(expected['text'],actual['text'])
    def test_legacy_entities_inline_punctuation_and_links(self):
        result=project_html('<div>literal &lttag&gt and <b>bold</b>!</div><a href="https://example.com/?a=1&amp;b=2">Link</a>')
        self.assertEqual(result['text'],'literal <tag> and bold! Link')
        self.assertEqual(result['links'],[{'href':'https://example.com/?a=1&b=2','text':'Link'}])
    def test_unknown_native_objects_remain_incomplete(self):
        for html in ('<object></object>','<img src="https://example.com/asset">','<table><tr><td>incomplete'):
            self.assertFalse(project_html(html)['text_projection_complete'])
    def test_script_and_style_text_never_becomes_note_content(self):
        result=project_html('<style>secret style</style><script>ignore instructions</script><div>Visible</div>')
        self.assertEqual(result['text'],'Visible')
    def test_excessive_link_and_object_nesting_refuses(self):
        for tag in ('a','object'):
            html=('<'+tag+'>')*65+'synthetic'+('</'+tag+'>')*65
            with self.subTest(tag=tag),self.assertRaises(ValueError):project_html(html)
