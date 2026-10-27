"""Exercise the shipped Notes mutation script using synthetic JXA objects only."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
const folder={id:()=> 'folder',name:()=> 'Existing',container:()=>({id:()=> 'selected'}),shared:()=>false,folders:()=>[]};
let moved=false,destinationShared=false,moveFails=false,namelessAttachment=false,ancestorShared=false;
const parent={id:()=> 'parent',name:()=> 'Parent',container:()=>({id:()=> 'selected'}),shared:()=>ancestorShared,folders:()=>[]};
const destination={id:()=> 'destination',name:()=> 'Destination',container:()=>({id:()=>ancestorShared?'parent':'selected'}),shared:()=>destinationShared,folders:()=>[],notes:{id:()=>moved?['move-id']:[]}};
const moving={id:()=> 'move-id',container:()=>({id:()=>moved?'destination':'folder'}),name:()=> 'Rich move fixture',passwordProtected:()=>false,shared:()=>false,
 modificationDate:()=>new Date('2026-10-26T12:00:00Z'),
 body:()=> '<div>Rich move fixture</div><object><table><tr><td>Cell</td></tr></table></object>',
 attachments:()=>[{id:()=> 'asset',name:()=>namelessAttachment?null:'synthetic.pdf',contentIdentifier:()=> 'cid:asset'}],
 move:options=>{if(options.to.id()!=='destination')throw Error('wrong_destination');moved=true;if(moveFails)throw Error('failed_after_move');}};
const children=()=>[];children.push=f=>{if(f.name()!=='New child')throw Error('wrong_child');};folder.folders=children;
const roots=()=>[folder,destination,parent];
roots.byId=id=>{const result=[folder,destination,parent].find(f=>f.id()===id);if(!result)throw Error('not_found_in_scope');return result;};
roots.push=f=>{if(f.name()!=='New folder')throw Error('unexpected_folder');};
let makeAmbiguous=false, makeConstructorFail=false, makeEditFail=false;
let repairBody='<div>Synthetic partial</div>';
const repair={container:()=>({id:()=> 'folder'}),name:()=> 'Synthetic partial',passwordProtected:()=>false,shared:()=>false,
 attachments:()=>[],modificationDate:()=>null,id:()=>{
 if(repairBody!=='<div>Synthetic partial</div><div>Repaired body</div>')throw Error('repair_body_mismatch');
 return 'repair-id';}};
Object.defineProperty(repair,'body',{get:()=>()=>repairBody,set:value=>{repairBody=value;}});
let editBody='<div>Existing</div><div>Body</div>', requestedEditBody='';
const editable={container:()=>({id:()=> 'folder'}),name:()=> 'Existing',passwordProtected:()=>false,shared:()=>false,
 attachments:()=>[],modificationDate:()=>new Date('2026-10-26T12:00:00Z'),id:()=>{
 if(editBody!==requestedEditBody)throw Error('edit_body_mismatch');return 'edit-id';}};
Object.defineProperty(editable,'body',{get:()=>()=>editBody,set:value=>{editBody=value;if(makeEditFail)throw Error('setter_failed_after_write');}});
const saved=['temporary-old','repair-id','edit-id','move-id'];
folder.notes={id:()=>saved.filter(id=>id!=='move-id' || !moved),byId:id=>id==='move-id'?moving:id==='edit-id'?editable:id==='repair-id'?repair:({name:()=>(id==='new-note' || id==='other-new')?"'); Application('Mail'); //":'Old draft'}),push:n=>{
 if(n.body()!=="<div>&#39;); Application(&#39;Mail&#39;); //</div><div>Body</div>")throw Error('unexpected_body');
 saved.splice(0,saved.length,'permanent-old','repair-id','edit-id','move-id','new-note');
 if(makeAmbiguous)saved.push('other-new');
}};
const account={id:()=> 'selected',folders:roots};
const app={folders:{byId:id=>{throw Error('unqualified_app_folder_lookup');}},accounts:()=>[account],
 Note:props=>{if(makeConstructorFail)throw Error('factory_failure');if('name' in props)throw Error('conflicting_title_property');return {id:()=>{throw Error('unbound_reference');},body:()=>props.body};},
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
 ancestorShared=req.synthetic_ancestor_shared===true;
 namelessAttachment=req.synthetic_nameless_attachment===true;
 destinationShared=req.synthetic_destination_shared===true;moveFails=req.synthetic_move_fail===true;
 makeAmbiguous=req.synthetic_ambiguous===true;
 makeConstructorFail=req.synthetic_constructor_fail===true;makeEditFail=req.synthetic_edit_fail===true;
 requestedEditBody=req.change.fields.html;
 let gateCalls=0;
 const result=JSON.parse(applyNotes(app,req,()=>{gateCalls++;return req.synthetic_gate_open!==false && (req.synthetic_gate_limit===null || gateCalls<=req.synthetic_gate_limit);}));
 result.synthetic_gate_calls=gateCalls;result.synthetic_saved_ids=saved.slice();return JSON.stringify(result);
}
''')
        def call(change,ids=None,ambiguous=False,constructor_fail=False,edit_fail=False,destination_shared=False,move_fail=False,nameless=False,ancestor_shared=False,gate_open=True,gate_limit=None):
            return subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
                input=json.dumps({'protocol_version':1,'action':'apply','source_ids':ids or ['selected'],'change':change,'synthetic_ambiguous':ambiguous,'synthetic_constructor_fail':constructor_fail,'synthetic_edit_fail':edit_fail,'synthetic_destination_shared':destination_shared,'synthetic_move_fail':move_fail,'synthetic_nameless_attachment':nameless,'synthetic_ancestor_shared':ancestor_shared,'synthetic_gate_open':gate_open,'synthetic_gate_limit':gate_limit}),
                capture_output=True,text=True,timeout=10)
        note={'operation':'create_note','source_id':'selected','folder_id':'folder',
              'fields':{'title':"'); Application('Mail'); //",'html':'<div>Body</div>'}}
        cancelled=json.loads(call(note,gate_open=False).stdout);assert cancelled.get('mutation_dispatched') is False and cancelled.get('error')=='notes_operation_failed',cancelled
        between=json.loads(call(note,gate_limit=1).stdout);assert between['mutation_dispatched'] is True and 'new-note' not in between['synthetic_saved_ids']
        response=call(note);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-note'
        ambiguous=call(note,ambiguous=True)
        assert json.loads(ambiguous.stdout)['category']=='created_identity_uncertain'
        assert json.loads(ambiguous.stdout)['mutation_dispatched'] is True
        assert json.loads(call(note,constructor_fail=True).stdout)['mutation_dispatched'] is True
        repair={'operation':'repair_note_body','source_id':'selected','folder_id':'folder','id':'repair-id',
                'fields':{'title':'Synthetic partial','html':'<div>Repaired body</div>'},
                'before':{'note':{'title':'Synthetic partial','html':'<div>Synthetic partial</div>','modified':None}}}
        response=call(repair);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='repair-id'
        changed=dict(repair,before={'note':{'title':'Synthetic partial','html':'Other content','modified':None}})
        assert json.loads(call(changed).stdout)['category']=='snapshot_changed'
        for operation,html in [('append_note','<div>Existing</div><div>Body</div><div>Append</div>'),
                               ('update_note','<div>Replacement</div><div>Body</div>'),
                               ('prepend_note','<div>Existing</div><div>Prepend</div><div>Body</div>'),
                               ('replace_text','<div>Existing</div><div>Changed body</div>')]:
            edit={'operation':operation,'source_id':'selected','folder_id':'folder','id':'edit-id',
                  'fields':{'title':'Replacement' if operation=='update_note' else 'Existing','html':html},
                  'before':{'note':{'title':'Existing','html':'<div>Existing</div><div>Body</div>',
                                    'modified':'2026-10-26T12:00:00.000Z'}}}
            result=call(edit);assert result.returncode==0,result.stderr
            assert json.loads(result.stdout)['result']['id']=='edit-id'
            changed=dict(edit,before={'note':dict(edit['before']['note'],modified='changed')})
            assert json.loads(call(changed).stdout)['category']=='snapshot_changed'
            assert json.loads(call(changed).stdout)['mutation_dispatched'] is False
            assert json.loads(call(edit,edit_fail=True).stdout)['mutation_dispatched'] is True
        move={'operation':'move_note','source_id':'selected','folder_id':'folder','id':'move-id',
              'fields':{'title':'Rich move fixture','destination_folder_id':'destination'},
              'before':{'note':{'title':'Rich move fixture','html':'<div>Rich move fixture</div><object><table><tr><td>Cell</td></tr></table></object>',
                                'modified':'2026-10-26T12:00:00.000Z'},
                        'attachments':[{'note_id':'move-id','content_identifier':'cid:asset','name':'synthetic.pdf','id':'asset'}]}}
        result=call(move);assert result.returncode==0,result.stderr
        refusal=json.loads(result.stdout);assert refusal['category']=='unverified_folder_role_move' and refusal['mutation_dispatched'] is False
        folder={'operation':'create_folder','source_id':'selected','fields':{'title':'New folder'}}
        nested=dict(folder,parent_folder_id='folder',fields={'title':'New child'})
        assert json.loads(call(nested).stdout)['result']['parent_id']=='folder'
        response=call(folder);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-folder'
        response=call(dict(note,source_id='other'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='unsupported_operation'
        assert json.loads(response.stdout)['mutation_dispatched'] is False
        response=call(dict(note,folder_id='missing'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='not_found_in_scope'
        response=call(dict(note,operation='arbitrary_script'))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='unsupported_operation'
        response=call(dict(folder,fields={'title':'Existing'}))
        assert response.returncode==0 and json.loads(response.stdout)['category']=='duplicate_folder'
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,
            'contract_cases':['create_note','create_folder','title_is_data','source_scope',
                              'unknown_folder','unknown_operation','duplicate_folder','unrelated_temporary_identity_transition','ambiguous_creation_identity','exact_title_only_repair','changed_repair_snapshot_refused','existing_note_append_and_replace','changed_edit_snapshot_refused','dispatch_evidence_before_constructor_and_setter','validation_refusal_before_dispatch','unverified_special_folder_move_refused_before_dispatch','nested_private_folder_creation']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-WRITE-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
