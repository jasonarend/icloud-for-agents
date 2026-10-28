"""Readable projections with honest native-object/format loss and no active HTML."""
import unittest
from icloud_for_agents.notes_markdown import project_markdown

class MarkdownTests(unittest.TestCase):
    def test_notes_normalized_nested_lists_preserve_container_indentation(self):
        p=project_markdown('<ul><li>Parent</li><ol><li>Child</li><li>Another</li></ol><li>Sibling</li></ul>')
        self.assertEqual(p['text'],'- Parent\n  1. Child\n  2. Another\n- Sibling')
        self.assertTrue(p['formatting_complete'])
        p=project_markdown('<ol start="10"><li>Parent</li><ul><li>Child</li><ol><li>Grandchild</li></ol></ul></ol>')
        self.assertEqual(p['text'],'10. Parent\n    - Child\n      1. Grandchild')

    def test_orphan_nested_list_reports_unknown_parent_structure(self):
        p=project_markdown('<ul><ol><li>Orphan</li></ol></ul>')
        self.assertIn('Orphan',p['text']);self.assertIn('list_structure',p['losses'])
        self.assertFalse(p['formatting_complete'])

    def test_headings_emphasis_links_lists_and_code_are_semantic(self):
        html='<h1>Product spec</h1><p><b>Decision</b>: <i>ship</i> <a href="https://example.com/spec">source</a>.</p><ul><li>First</li><li>Second<ul><li>Child</li></ul></li></ul><pre>literal ``` code</pre>'
        p=project_markdown(html);self.assertIn('# Product spec',p['text']);self.assertIn('**Decision**',p['text']);self.assertIn('*ship*',p['text']);self.assertIn('[source](<https://example.com/spec>)',p['text']);self.assertIn('- First',p['text']);self.assertIn('  - Child',p['text']);self.assertIn('````\nliteral ``` code\n````',p['text']);self.assertTrue(p['text_complete']);self.assertFalse(p['round_trip_safe']);self.assertEqual(p['native_fidelity'],'unverified')
    def test_table_wrappers_keep_cells_without_inventing_a_header(self):
        p=project_markdown('<object><table><tr><td>A</td><td><b>B</b></td></tr><tr><td>x|y</td><td>2</td></tr></table></object>')
        self.assertIn('|  |  |\n| --- | --- |\n| A | **B** |',p['text']);self.assertIn('x\\|y',p['text']);self.assertTrue(p['text_complete']);self.assertIn('native_object_metadata_not_represented',p['losses'])
    def test_merged_and_nested_tables_report_lost_layout(self):
        p=project_markdown('<table><tr><th colspan="2">Header</th></tr><tr><td><table><tr><td>nested</td></tr></table></td><td>end</td></tr></table>');self.assertIn('table_merges',p['losses']);self.assertIn('nested_table_layout',p['losses']);self.assertIn('nested',p['text']);self.assertFalse(p['formatting_complete'])
    def test_active_content_assets_and_credential_links_are_not_exported(self):
        p=project_markdown('<style>hidden</style><script>doBad()</script><p>Visible</p><img src="https://example.com/secret"><a href="javascript:evil()">Label</a><a href="https://user:secret@example.com/">Private link</a>')
        self.assertNotIn('doBad',p['text']);self.assertNotIn('example.com',p['text']);self.assertIn('Visible',p['text']);self.assertIn('Native object omitted',p['text']);self.assertIn('unsupported_link_omitted',p['losses']);self.assertFalse(p['text_complete'])
    def test_literal_markdown_and_raw_html_are_escaped(self):
        p=project_markdown('<p>![image](https://example.com/a) &lt;script&gt; *literal*</p>');self.assertIn('\\!\\[image\\]',p['text']);self.assertIn('\\<script\\>',p['text']);self.assertIn('\\*literal\\*',p['text'])
    def test_truncation_and_malformed_html_are_not_complete_exports(self):
        for html,kw in [('<p>Visible</p>',{'source_truncated':True}),('<p>unfinished',{}),('<p>'+'*'*60000+'</p>',{})]:
            p=project_markdown(html,**kw);self.assertFalse(p['text_complete']);self.assertFalse(p['round_trip_safe']);self.assertLessEqual(len(p['text'].encode()),100000)
        with self.assertRaises(ValueError):project_markdown('<div>'*65+'x'+'</div>'*65)
    def test_visual_styles_and_underline_have_explicit_loss(self):
        p=project_markdown('<div style="color:red"><u>Underlined</u><span>text</span></div>');self.assertIn('Underlined',p['text']);self.assertIn('visual_attributes_not_represented',p['losses']);self.assertIn('unsupported_formatting',p['losses']);self.assertFalse(p['formatting_complete'])

    def test_unknown_closing_root_never_breaks_parser_and_objects_remain_visible(self):
        p=project_markdown('</root><object></object><p>Visible</p>');self.assertIn('Visible',p['text']);self.assertIn('Native object omitted',p['text']);self.assertIn('malformed_html',p['losses']);self.assertFalse(p['text_complete'])
    def test_pipe_in_inline_code_stays_inside_table_cell(self):
        p=project_markdown('<table><tr><td><code>a|b</code></td><td>other</td></tr></table>');self.assertIn('a\\|b',p['text'])
