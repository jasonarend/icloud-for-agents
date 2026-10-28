"""Plain table formatting escapes content and never asserts native fidelity."""
import unittest
from icloud_for_agents.core import SafetyError
from icloud_for_agents.native_notes import prepare_content
from icloud_for_agents.notes_content import project_html
from icloud_for_agents.notes_table_format import format_table


class NotesTableFormatTests(unittest.TestCase):
    def test_escaped_cells_reach_notes_validator_without_execution(self):
        rows=[['Title','Value'],['<script>&','"quoted"']]
        result=format_table(rows)
        self.assertEqual(project_html(result['body'])['tables'],[rows])
        self.assertIn('&lt;script&gt;&amp;',result['body'])
        self.assertFalse(result['coverage']['native_table_identity'])
        self.assertFalse(result['coverage']['round_trip_safe'])
        self.assertEqual(prepare_content({'title':'Synthetic','body':result['body'],'format':'html'})['html'],result['body'])

    def test_invalid_shapes_content_and_sizes_refused(self):
        for rows in ([],[[]],[['one'],['two','three']],[[None]],[['bad\ncell']],
                     [['bad\x00cell']],[['\ud800']], [['x']*21], [['x']]*101, [['x'*4001]],
                     [['x'*4000]*20,['y'*4000]*20]):
            with self.subTest(rows=repr(rows)[:80]),self.assertRaises(SafetyError):format_table(rows)

if __name__=='__main__':unittest.main()
