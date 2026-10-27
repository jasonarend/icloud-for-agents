"""Actual fixed read/write scripts on metadata-only nested Notes fixtures."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE=r'''
let title='Original',shared=false,defaultID='default-other',overflow=false;
const metadataNote=folder=>({container:()=>({id:()=>folder}),body:()=>{throw Error('body_read');},passwordProtected:()=>{throw Error('lock_read');}});
const child={id:()=> 'child',name:()=> 'Child',container:()=>({id:()=> 'folder'}),shared:()=>false,folders:()=>[],notes:{id:()=>['locked'],byId:id=>metadataNote('child')}};
const folder={id:()=> 'folder',container:()=>({id:()=> 'selected'}),shared:()=>shared,folders:()=>[child],notes:{id:()=>overflow?Array.from({length:201},(_,i)=>'note-'+i):['rich','phantom-child'],byId:id=>metadataNote(id==='phantom-child'?'child':'folder')}};
Object.defineProperty(folder,'name',{get:()=>()=>title,set:value=>{title=value;}});
const sibling={id:()=> 'sibling',name:()=> 'Sibling',container:()=>({id:()=> 'selected'})};
const roots=()=>[folder,child,sibling];roots.id=()=>['folder','child','sibling'];roots.byId=id=>{const result=[folder,child,sibling].find(f=>f.id()===id);if(!result)throw Error('not_found_in_scope');return result;};
const account={id:()=> 'selected',folders:roots,defaultFolder:()=>({id:()=>defaultID})};
const app={accounts:()=>[account],Note:()=>{throw Error('constructor_called');},Folder:()=>{throw Error('constructor_called');}};
'''

def run():
    with tempfile.TemporaryDirectory(prefix='native-folder-inventory-') as directory:
        driver='''
function run() {
 const data=$.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
 const req=JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data,$.NSUTF8StringEncoding)));
 shared=req.synthetic_shared===true;defaultID=req.synthetic_default?'folder':'default-other';overflow=req.synthetic_overflow===true;
 if(req.action==='apply') {let entries=0;let result=JSON.parse(applyNotes(app,req,()=>{entries++;return req.synthetic_gate!==false;},req.synthetic_phase));result.title=title;result.entries=entries;return JSON.stringify(result);}
 return readNotes(app,req);
}
'''
        paths={}
        for action,name in [('read','notes.js'),('apply','notes-write.js')]:
            path=Path(directory)/(action+'.js');path.write_text(FIXTURE+'\n'+(ROOT/'src/icloud_for_agents/native'/name).read_text()+driver);paths[action]=path
        def call(action,**fields):
            return subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(paths['apply' if action=='apply' else 'read'])],input=json.dumps({'protocol_version':1,'service':'notes','action':action,'source_ids':['selected'],'folder_id':'folder',**fields}),capture_output=True,text=True,timeout=15)
        value=call('folder_inventory');assert value.returncode==0,value.stderr
        inventory=json.loads(value.stdout);inventory.pop('protocol_version')
        assert [f['id'] for f in inventory['folders']]==['child','folder']
        assert inventory['notes']==[{'id':'locked','folder_id':'child'},{'id':'rich','folder_id':'folder'}]
        assert inventory['siblings']==[{'id':'sibling','title':'Sibling'}]
        change={'operation':'rename_folder','source_id':'selected','folder_id':'folder','id':'folder','fields':{'title':"Quoted ' title"},'writer_backend':'scripting','legacy_title_restore':True,'before':{'inventory':inventory,'role':{'title':"Quoted ' title"}}}
        result=json.loads(call('apply',change=change).stdout);assert result['result']['id']=='folder' and result['title']=="Quoted ' title" and result['entries']==1
        native=dict(change,writer_backend='notes-shared')
        result=json.loads(call('apply',change=native,synthetic_phase='validate_model').stdout);assert result['validated'] is True and result['entries']==0 and result['title']=='Original'
        result=json.loads(call('apply',change=native).stdout);assert result['mutation_dispatched'] is False and result['title']=='Original'
        for flags in ({'synthetic_gate':False},{'synthetic_default':True},{'synthetic_shared':True},{'synthetic_overflow':True}):
            result=json.loads(call('apply',change=change,**flags).stdout);assert result['mutation_dispatched'] is False and result['title']=='Original',flags
        bad=dict(change,before={'inventory':dict(inventory,notes=[])})
        assert json.loads(call('apply',change=bad).stdout)['mutation_dispatched'] is False
        duplicate=dict(change,fields={'title':'Sibling'})
        assert json.loads(call('apply',change=duplicate).stdout)['category']=='duplicate_folder'
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,'checks':['nested_complete_membership','physical_container_filter','locked_and_rich_bodies_never_read','native_name_setter_only','exact_prewrite_snapshot','default_shared_and_overflow_refusal','dispatch_gate_before_setter','duplicate_sibling_refusal']}
if __name__=='__main__':
    result=run();(ROOT/'build/reports/NATIVE-FOLDER-INVENTORY-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
