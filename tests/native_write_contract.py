"""Exercise the shipped Notes mutation script using synthetic JXA objects only."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
const folder={id:()=> 'folder',name:()=> 'Existing',container:()=>({id:()=> 'selected'}),shared:()=>false,folders:()=>[]};
const roots=()=>[folder];
roots.push=f=>{if(f.name()!=='New folder')throw Error('unexpected_folder');};
let makeAmbiguous=false;
let repairBody='<div>Synthetic partial</div>';
const repair={name:()=> 'Synthetic partial',passwordProtected:()=>false,shared:()=>false,
 attachments:()=>[],modificationDate:()=>null,id:()=>{
 if(repairBody!=='<div>Synthetic partial</div><div>Repaired body</div>')throw Error('repair_body_mismatch');
 return 'repair-id';}};
Object.defineProperty(repair,'body',{get:()=>()=>repairBody,set:value=>{repairBody=value;}});
let editBody='<div>Existing</div><div>Body</div>', requestedEditBody='';
const editable={name:()=> 'Existing',passwordProtected:()=>false,shared:()=>false,
 attachments:()=>[],modificationDate:()=>new Date('2026-10-26T12:00:00Z'),id:()=>{
 if(editBody!==requestedEditBody)throw Error('edit_body_mismatch');return 'edit-id';}};
Object.defineProperty(editable,'body',{get:()=>()=>editBody,set:value=>{editBody=value;}});
const saved=['temporary-old','repair-id','edit-id'];
folder.notes={id:()=>saved.slice(),byId:id=>id==='edit-id'?editable:id==='repair-id'?repair:({name:()=>(id==='new-note' || id==='other-new')?"'); Application('Mail'); //":'Old draft'}),push:n=>{
 if(n.body()!=="<div>&#39;); Application(&#39;Mail&#39;); //</div><div>Body</div>")throw Error('unexpected_body');
 saved.splice(0,saved.length,'permanent-old','repair-id','edit-id','new-note');
 if(makeAmbiguous)saved.push('other-new');
}};
const account={id:()=> 'selected',folders:roots};
const app={accounts:()=>[account],
 Note:props=>{if('name' in props)throw Error('conflicting_title_property');return {id:()=>{throw Error('unbound_reference');},body:()=>props.body};},
 Folder:props=>({id:()=> 'new-folder',name:()=>props.name})};
'''


def run():
    with tempfile.TemporaryDirectory(prefix='icloud-native-write-fixtures-') as directory:
        source=(ROOT/'src/icloud_for_agents/native/notes-write.js').read_text()
        script=Path(directory)/'write-fixture.js'
        script.write_text(FIXTURE+'\n'+source+'''
function run() {
 const data=$.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
 const req=JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data,$.NSUTF8StringEncoding)));
 makeAmbiguous=req.synthetic_ambiguous===true;
 requestedEditBody=req.change.fields.html;
 return applyNotes(app,req);
}
''')
        def call(change,ids=None,ambiguous=False):
            return subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
                input=json.dumps({'protocol_version':1,'action':'apply','source_ids':ids or ['selected'],'change':change,'synthetic_ambiguous':ambiguous}),
                capture_output=True,text=True,timeout=10)
        note={'operation':'create_note','source_id':'selected','folder_id':'folder',
              'fields':{'title':"'); Application('Mail'); //",'html':'<div>Body</div>'}}
        response=call(note);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-note'
        ambiguous=call(note,ambiguous=True)
        assert json.loads(ambiguous.stdout)['category']=='created_identity_uncertain'
        repair={'operation':'repair_note_body','source_id':'selected','folder_id':'folder','id':'repair-id',
                'fields':{'title':'Synthetic partial','html':'<div>Repaired body</div>'},
                'before':{'note':{'title':'Synthetic partial','html':'<div>Synthetic partial</div>','modified':None}}}
        response=call(repair);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='repair-id'
        changed=dict(repair,before={'note':{'title':'Synthetic partial','html':'Other content','modified':None}})
        assert json.loads(call(changed).stdout)['category']=='snapshot_changed'
        for operation,html in [('append_note','<div>Existing</div><div>Body</div><div>Append</div>'),
                               ('update_note','<div>Replacement</div><div>Body</div>')]:
            edit={'operation':operation,'source_id':'selected','folder_id':'folder','id':'edit-id',
                  'fields':{'title':'Existing' if operation=='append_note' else 'Replacement','html':html},
                  'before':{'note':{'title':'Existing','html':'<div>Existing</div><div>Body</div>',
                                    'modified':'2026-10-26T12:00:00.000Z'}}}
            result=call(edit);assert result.returncode==0,result.stderr
            assert json.loads(result.stdout)['result']['id']=='edit-id'
            changed=dict(edit,before={'note':dict(edit['before']['note'],modified='changed')})
            assert json.loads(call(changed).stdout)['category']=='snapshot_changed'
        folder={'operation':'create_folder','source_id':'selected','fields':{'title':'New folder'}}
        response=call(folder);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-folder'
        response=call(dict(note,source_id='other'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='unsupported_operation'
        response=call(dict(note,folder_id='missing'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='not_found_in_scope'
        response=call(dict(note,operation='arbitrary_script'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='unsupported_operation'
        response=call(dict(folder,fields={'title':'Existing'}))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='duplicate_folder'
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,
            'contract_cases':['create_note','create_folder','title_is_data','source_scope',
                              'unknown_folder','unknown_operation','duplicate_folder','unrelated_temporary_identity_transition','ambiguous_creation_identity','exact_title_only_repair','changed_repair_snapshot_refused','existing_note_append_and_replace','changed_edit_snapshot_refused']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-WRITE-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
