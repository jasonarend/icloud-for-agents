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
 {id:()=> 'note-1',name:()=> 'Synthetic plain',container:()=>({id:()=> 'folder-1'}),passwordProtected:()=> false,shared:()=>false,
  attachments:()=>[{id:()=> 'attachment-1',name:()=> null,contentIdentifier:()=> 'cid:synthetic',creationDate:()=>new Date('2026-01-01T00:00:00Z'),modificationDate:()=>null,url:()=>{throw Error('missing_url')},shared:()=>false}],body:()=> '<div>Synthetic content</div><object><table><tr><td>Table-only token</td></tr></table></object>',plaintext:()=> 'Synthetic content',
  creationDate:()=> new Date('2026-01-01T00:00:00Z'),modificationDate:()=> null},
 {id:()=> 'note-2',name:()=> 'Synthetic locked',container:()=>({id:()=> 'folder-1'}),passwordProtected:()=> true,
  body:()=> {throw Error('locked_content_was_read');},plaintext:()=> {throw Error('locked_content_was_read');}}
];
const notesCollection = ()=>fixtureNotes;
notesCollection.id=()=>fixtureNotes.map(n=>n.id());
notesCollection.byId=id=>fixtureNotes.find(n=>n.id()===id);
let fixtureNoWalk=false;
const child = {shared:()=>false,id:()=> 'folder-child',name:()=> 'Child',container:()=>({id:()=> 'folder-1'}),folders:()=>[],notes:notesCollection};
const parent = {shared:()=>false,id:()=> 'folder-1',name:()=> 'Synthetic',container:()=>({id:()=> 'selected'}),folders:()=>[child],notes:notesCollection};
const unselected = {id:()=> 'unselected',name:()=> 'Other',folders:()=> {throw Error('unselected_account_read');}};
const foreignFolder={id:()=> 'foreign-folder',container:()=>({id:()=> 'unselected'}),name:()=>{throw Error('foreign_metadata_read');}};
const cycleFolder={id:()=> 'cycle-folder',container:()=>({id:()=> 'cycle-folder'})};
const accountFolders=()=>{if(fixtureNoWalk)throw Error('unrelated_folder_walk');return [parent,child];};
accountFolders.id=()=>['folder-1','folder-child'];
accountFolders.byId=id=>{const f=[parent,child].find(f=>f.id()===id);if(!f)throw Error('not_found_in_scope');return f;};
const fixtureApplication = name=>{
 if(name!=='Notes')throw Error('unexpected_app');
 return {folders:{byId:id=>{throw Error('unqualified_app_folder_lookup');}},accounts:()=>[{id:()=> 'selected',name:()=> 'Synthetic account',folders:accountFolders,defaultFolder:()=>parent},unselected]};
};
'''


def run():
    source = (ROOT/'src/icloud_for_agents/native/notes.js').read_text()
    with tempfile.TemporaryDirectory(prefix='icloud-native-fixtures-') as directory:
        script=Path(directory)/'notes-fixture.js';script.write_text(FIXTURE+'\n'+source+"\nfunction run() { const req=readRequest();fixtureNoWalk=req.synthetic_no_walk===true;if(req.synthetic_long)fixtureNotes[0].plaintext=()=> 'A'.repeat(100005)+'🚀Z';if(req.synthetic_huge)fixtureNotes[0].plaintext=()=> 'A'.repeat(4999999)+'🚀Z';if(req.synthetic_modified)fixtureNotes[0].modificationDate=()=>new Date('2026-10-27T12:00:00.000Z');if(req.synthetic_title_only)fixtureNotes.forEach(n=>{n.body=()=>{throw Error('body_read');};n.plaintext=()=>{throw Error('body_read');};});return readNotes(fixtureApplication('Notes'),req); }\n")
        def call(action, **fields):
            request={'protocol_version':1,'action':action,'source_ids':['selected'],**fields}
            process=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
                input=json.dumps(request),capture_output=True,text=True,timeout=10)
            if process.returncode:
                raise AssertionError('Synthetic JXA failed: '+process.stderr)
            return json.loads(process.stdout)
        # Exercise OSAKit too: this is the embedding API used by our signed helper.
        swift=Path(directory)/'bridge.swift'
        swift.write_text("""import Foundation
import OSAKit
let source = try String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8)
let script = OSAScript(source: source, language: OSALanguage(forName: \"JavaScript\"))
var error: NSDictionary?
guard let result = script.executeAndReturnError(&error)?.stringValue else { exit(2) }
print(result)
""")
        bridge=Path(directory)/'bridge'
        subprocess.run(['/usr/bin/swiftc',str(swift),'-o',str(bridge)],capture_output=True,check=True,timeout=60)
        embedded=subprocess.run([str(bridge),str(script)],input=json.dumps({'protocol_version':1,'action':'get','source_ids':['selected'],'folder_id':'folder-1','id':'note-1'}),capture_output=True,text=True,timeout=10)
        assert embedded.returncode==0,embedded.stderr
        assert json.loads(embedded.stdout)['item']['text']=='Synthetic content'
        page=call('text_page',folder_id='folder-1',id='note-1',offset=100000,limit=6,synthetic_long=True,synthetic_modified=True)
        assert page['text']=='AAAAA🚀' and page['next_offset']==100007 and not page['source_truncated'] and page['offset_unit']=='utf16_code_units'
        last=call('text_page',folder_id='folder-1',id='note-1',offset=page['next_offset'],limit=10,expected_modified=page['modified'],synthetic_long=True,synthetic_modified=True)
        assert last['text']=='Z' and last['done']
        capped=call('text_page',folder_id='folder-1',id='note-1',offset=4999998,limit=10,synthetic_huge=True);assert capped['text']=='A' and capped['done'] and capped['source_truncated'] and capped['next_offset']==4999999
        assert call('text_page',folder_id='folder-1',id='note-1',offset=100006,limit=10,synthetic_long=True)['error']=='invalid_page'
        assert call('text_page',folder_id='folder-1',id='note-1',offset=0,limit=10,expected_modified='different',synthetic_modified=True)['error']=='snapshot_changed'
        locked=call('text_page',folder_id='folder-1',id='note-2',offset=0,limit=10);assert locked['locked'] and 'text' not in locked
        assert call('contains',folder_id='folder-1',id='note-1')['exists'] is True
        default=call('default_folder',source_id='selected')['folder'];assert default['id']=='folder-1' and default['role']=='default_folder' and default['source_id']=='selected'
        assert call('contains',folder_id='folder-1',id='missing')['exists'] is False
        assert call('contains',folder_id='folder-child',id='note-1')['exists'] is False
        child_page=call('query',folder_id='folder-child',limit=1,offset=0)
        assert child_page['items']==[] and child_page['next_offset']==1 and child_page['done'] is False
        exact=call('folder_get',folder_id='folder-child',synthetic_no_walk=True)['folder']
        assert exact['parent_id']=='folder-1' and exact['source_id']=='selected'
        path=call('folder_get',folder_id='folder-child',synthetic_no_walk=True)['ancestors']
        assert len(path)==1 and path[0]['id']=='folder-1' and path[0]['shared'] is False
        assert call('get',folder_id='folder-1',id='note-1',synthetic_no_walk=True)['item']['id']=='note-1'
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
        assert note['modified'] is None
        diagnostic=call('diagnose_note',folder_id='folder-1',id='note-2')['item']
        assert diagnostic['locked'] is True and diagnostic['content_available'] is False
        locked=call('get',folder_id='folder-1',id='note-2')['item']
        assert locked['locked'] is True and locked['content_available'] is False
        assert 'html' not in locked and 'text' not in locked
        search=call('search',text='content',limit=1,offset=0)
        assert len(search['items'])==1 and search['items'][0]['id']=='note-1'
        search=call('search',text='absent',limit=100,offset=0)
        assert search['items']==[] and search['locked_bodies_excluded']>0
        table_search=call('search',text='Table-only',limit=1,offset=0)
        assert len(table_search['table_projection_candidates'])==1
        bounded=call('search',text='Table-only',folder_id='folder-1',limit=10,offset=0,synthetic_modified=True,modified_from='2026-10-27T12:00:00Z',modified_before='2026-10-27T12:00:01Z')
        assert bounded['table_projection_candidates'][0]['modified']=='2026-10-27T12:00:00.000Z'
        assert bounded['modified_filter']['semantics']=='utc_inclusive_from_exclusive_before'
        outside=call('search',text='content',folder_id='folder-1',limit=10,offset=0,synthetic_modified=True,modified_before='2026-10-27T12:00:00Z')
        assert outside['items']==[]
        titles=call('search',text='Synthetic',folder_id='folder-1',limit=10,offset=0,match_scope='title',synthetic_title_only=True)
        assert [x['id'] for x in titles['items']]==['note-1','note-2'] and titles['unavailable']==[] and titles['table_projection_candidates']==[] and titles['match_scope']=='title'
        locked_titles=call('search',text='',folder_id='folder-1',limit=10,offset=0,match_scope='title',locked=True,synthetic_title_only=True)
        assert [x['id'] for x in locked_titles['items']]==['note-2'] and locked_titles['locked_filter'] is True
        unlocked_titles=call('search',text='',folder_id='folder-1',limit=10,offset=0,match_scope='title',locked=False,synthetic_title_only=True)
        assert [x['id'] for x in unlocked_titles['items']]==['note-1'] and unlocked_titles['locked_filter'] is False
        recent=call('search',text='',folder_id='folder-1',limit=10,offset=0,match_scope='title',synthetic_title_only=True,synthetic_modified=True,modified_from='2026-10-27T12:00:00Z')
        assert [x['id'] for x in recent['items']]==['note-1'] and recent['match_scope']=='title'
        filtered_titles=call('search',text='Synthetic',folder_id='folder-1',limit=10,offset=0,match_scope='title',synthetic_title_only=True,synthetic_modified=True,modified_from='2026-10-27T12:00:00Z',modified_before='2026-10-27T12:00:01Z')
        assert [x['id'] for x in filtered_titles['items']]==['note-1'] and filtered_titles['items'][0]['modified']=='2026-10-27T12:00:00.000Z'
        assert filtered_titles['match_scope']=='title' and filtered_titles['modified_filter']['semantics']=='utc_inclusive_from_exclusive_before'
        attachments=call('attachments',folder_id='folder-1',id='note-1',limit=1,offset=0)
        assert attachments['attachments'][0]['id']=='attachment-1' and attachments['content_downloaded'] is False
        assert attachments['attachments'][0]['name'] is None
        detailed=call('attachments',folder_id='folder-1',id='note-1',limit=1,offset=0,include_attachment_metadata=True);meta=detailed['attachments'][0]['native_metadata']
        assert detailed['metadata_requested'] and detailed['source_id']=='selected' and meta['created']=={'status':'available','value':'2026-01-01T00:00:00.000Z'} and meta['modified']=={'status':'available','value':None} and meta['url']=={'status':'unavailable','value':None} and meta['shared']=={'status':'available','value':False}

        locked_attachments=call('attachments',folder_id='folder-1',id='note-2',limit=1,offset=0)
        assert locked_attachments['locked'] is True and locked_attachments['content_available'] is False
        detailed_locked=call('attachments',folder_id='folder-1',id='note-2',limit=1,offset=0,include_attachment_metadata=True);assert detailed_locked['metadata_requested'] and detailed_locked['attachments']==[]
        invalid=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
            input=json.dumps({'protocol_version':1,'action':'get','source_ids':['selected'],
                              'folder_id':'missing','id':'note-1'}),capture_output=True,text=True,timeout=10)
        assert invalid.returncode!=0 and 'not_found_in_scope' in invalid.stderr
        alias=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],input=json.dumps({'protocol_version':1,'action':'get','source_ids':['selected'],'folder_id':'folder-child','id':'note-1'}),capture_output=True,text=True,timeout=10)
        assert alias.returncode!=0 and 'not_found_in_scope' in alias.stderr
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,
            'contract_cases':['nested_folder_deduplication','account_scope','bounded_pages',
                              'exact_plain_note','locked_content_not_read','unknown_folder_refused','osakit_embedding','scoped_plaintext_search','locked_search_exclusion','attachment_metadata_only','nullable_native_attachment_name','exact_physical_container','exact_folder_reads_skip_unrelated_tree','bounded_physical_ancestor_metadata','empty_filtered_page_progression']}


if __name__=='__main__':
    result=run()
    (ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-SCRIPT-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
