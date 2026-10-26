"""Run the actual JXA read script against synthetic app objects; no Apple events.

Separate from the subprocess-forbidden offline suite. No live permission or content
is used, and these contract tests do not establish native-app acceptance.
"""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
const fixtureNotes = [
 {id:()=> 'note-1',name:()=> 'Synthetic plain',passwordProtected:()=> false,
  body:()=> '<div>Synthetic content</div>',plaintext:()=> 'Synthetic content',
  modificationDate:()=> new Date('2026-01-01T00:00:00Z')},
 {id:()=> 'note-2',name:()=> 'Synthetic locked',passwordProtected:()=> true,
  body:()=> {throw Error('locked_content_was_read');},plaintext:()=> {throw Error('locked_content_was_read');}}
];
const notesCollection = ()=>fixtureNotes;
notesCollection.id=()=>fixtureNotes.map(n=>n.id());
notesCollection.byId=id=>fixtureNotes.find(n=>n.id()===id);
const child = {id:()=> 'folder-child',name:()=> 'Child',container:()=>({id:()=> 'folder-1'}),folders:()=>[],notes:notesCollection};
const parent = {id:()=> 'folder-1',name:()=> 'Synthetic',container:()=>({id:()=> 'selected'}),folders:()=>[child],notes:notesCollection};
const unselected = {id:()=> 'unselected',name:()=> 'Other',folders:()=> {throw Error('unselected_account_read');}};
const fixtureApplication = name=>{
 if(name!=='Notes')throw Error('unexpected_app');
 return {accounts:()=>[{id:()=> 'selected',name:()=> 'Synthetic account',folders:()=>[parent,child]},unselected]};
};
'''


def run():
    source = (ROOT/'src/icloud_for_agents/native/notes.js').read_text()
    with tempfile.TemporaryDirectory(prefix='icloud-native-fixtures-') as directory:
        script=Path(directory)/'notes-fixture.js';script.write_text(FIXTURE+'\n'+source+"\nfunction run() { return readNotes(fixtureApplication('Notes'), readRequest()); }\n")
        def call(action, **fields):
            request={'protocol_version':1,'action':action,'source_ids':['selected'],**fields}
            process=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
                input=json.dumps(request),capture_output=True,text=True,timeout=10)
            if process.returncode:
                raise AssertionError('Synthetic JXA failed: '+process.stderr)
            return json.loads(process.stdout)
        folders=call('folders')['folders']
        assert len(folders)==2 and len({f['id'] for f in folders})==2,folders
        assert next(f for f in folders if f['id']=='folder-child')['parent_id']=='folder-1'
        folder_page=call('folders',limit=1,offset=0)
        assert len(folder_page['folders'])==1 and folder_page['done'] is False
        first=call('query',folder_id='folder-1',limit=1,offset=0)
        second=call('query',folder_id='folder-1',limit=1,offset=1)
        assert first['done'] is False and second['done'] is True
        assert first['items'][0]['id']!=second['items'][0]['id']
        note=call('get',folder_id='folder-1',id='note-1')['item']
        assert note['text']=='Synthetic content' and note['content_complete'] is False
        locked=call('get',folder_id='folder-1',id='note-2')['item']
        assert locked['locked'] is True and locked['content_available'] is False
        assert 'html' not in locked and 'text' not in locked
        invalid=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
            input=json.dumps({'protocol_version':1,'action':'get','source_ids':['selected'],
                              'folder_id':'missing','id':'note-1'}),capture_output=True,text=True,timeout=10)
        assert invalid.returncode!=0 and 'not_found_in_scope' in invalid.stderr
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,
            'contract_cases':['nested_folder_deduplication','account_scope','bounded_pages',
                              'exact_plain_note','locked_content_not_read','unknown_folder_refused']}


if __name__=='__main__':
    result=run()
    (ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-SCRIPT-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
