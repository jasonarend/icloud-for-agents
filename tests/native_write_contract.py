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
const saved=[];
folder.notes={id:()=>saved.slice(),push:n=>{
 if(n.body()!=="<div>&#39;); Application(&#39;Mail&#39;); //</div><div>Body</div>")throw Error('unexpected_body');
 saved.push('new-note');
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
 return applyNotes(app,req);
}
''')
        def call(change,ids=None):
            return subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(script)],
                input=json.dumps({'protocol_version':1,'action':'apply','source_ids':ids or ['selected'],'change':change}),
                capture_output=True,text=True,timeout=10)
        note={'operation':'create_note','source_id':'selected','folder_id':'folder',
              'fields':{'title':"'); Application('Mail'); //",'html':'<div>Body</div>'}}
        response=call(note);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-note'
        folder={'operation':'create_folder','source_id':'selected','fields':{'title':'New folder'}}
        response=call(folder);assert response.returncode==0,response.stderr
        assert json.loads(response.stdout)['result']['id']=='new-folder'
        response=call(dict(note,source_id='other'))
        assert response.returncode!=0 and 'unsupported_operation' in response.stderr
        response=call(dict(note,folder_id='missing'))
        assert response.returncode!=0 and 'not_found_in_scope' in response.stderr
        response=call(dict(note,operation='arbitrary_script'))
        assert response.returncode!=0 and 'unsupported_operation' in response.stderr
        response=call(dict(folder,fields={'title':'Existing'}))
        assert response.returncode!=0 and 'duplicate_folder' in response.stderr
    return {'status':'passed','synthetic_only':True,'native_app_acceptance':False,
            'contract_cases':['create_note','create_folder','title_is_data','source_scope',
                              'unknown_folder','unknown_operation','duplicate_folder']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-WRITE-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
