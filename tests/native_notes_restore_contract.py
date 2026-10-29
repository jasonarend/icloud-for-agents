"""Shipped system-recovery restore script with fixed synthetic JXA providers."""
import json,subprocess,tempfile
from pathlib import Path
from native_write_contract import FIXTURE,ROOT

def run():
    with tempfile.TemporaryDirectory(prefix='native-notes-restore-') as directory:
        script=Path(directory)/'restore.js';source=(ROOT/'src/icloud_for_agents/native/notes-write.js').read_text()
        script.write_text(FIXTURE+'\n'+source+'''
function run(){
 const bytes=$.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
 const req=JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(bytes,$.NSUTF8StringEncoding)));
 moveFails=req.synthetic_move_fails===true;
 if(req.synthetic_foreign_parent)folder.container=()=>({id:()=> 'parent'});
 let calls=0;const result=JSON.parse(applyNotes(app,req,()=>{calls++;return req.synthetic_gate!==false;}));result.gates=calls;result.moved=moved;return JSON.stringify(result);
}
''')
        def call(change,**extra):
            value=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],input=json.dumps({'protocol_version':1,'action':'apply','source_ids':['selected'],'change':change,**extra}),capture_output=True,text=True,timeout=10);assert value.returncode==0,value.stderr;return json.loads(value.stdout)
        role=lambda identity,title,kind:{'folder_id':identity,'source_id':'selected','title':title,'storage_kind':'special_smart_or_deleted' if kind else 'regular','native_folder_type':kind,'marked_for_deletion':False,'has_smart_query':False,'parent_pk':None}
        note={'title':'Rich move fixture','html':'<div>Rich move fixture</div><object><table><tr><td>Cell</td></tr></table></object>','modified':'2026-10-26T12:00:00.000Z'}
        change={'operation':'restore_note','source_id':'selected','folder_id':'folder','id':'move-id','folder_guard':'verified_recovery_roles','fields':{'title':'Rich move fixture','destination_folder_id':'destination'},'before':{'note':note,'roles':[role('folder','Existing',1),role('destination','Destination',0)],'attachments':[{'id':'asset','name':'synthetic.pdf','content_identifier':'cid:asset','note_id':'move-id'}]}}
        good=call(change);assert good['result']['id']=='move-id' and good['moved'] and good['gates']==1,good
        closed=call(change,synthetic_gate=False);assert closed['mutation_dispatched'] is False and not closed['moved'] and closed['gates']==1,closed
        for index,key,value in ((0,'native_folder_type',0),(0,'storage_kind','regular'),(0,'marked_for_deletion',True),(0,'parent_pk',1),(0,'source_id','other'),(1,'native_folder_type',1),(1,'has_smart_query',True),(1,'title','Changed')):
            bad=json.loads(json.dumps(change));bad['before']['roles'][index][key]=value;result=call(bad);assert result.get('error') and result['gates']==0 and not result['moved'],result
        bad=dict(change,folder_guard='verified_store_roles');assert call(bad)['gates']==0
        ordinary=dict(change,operation='move_note',folder_guard='verified_store_roles');assert call(ordinary)['gates']==0
        failed=call(change,synthetic_move_fails=True);assert failed['mutation_dispatched'] is True and failed['moved'],failed
    return {'status':'passed','apple_data_opened':False,'rich_note_restore_and_exact_role_guard':True,'closed_gate_and_failed_move_uncertainty':True}
if __name__=='__main__':print(json.dumps(run()))
