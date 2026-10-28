"""Test shipped model-role admission logic using synthetic values only."""
import json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    code=source[source.index('func notesModelRoleMatches('):source.index('let data = requestData()')]
    apply=source[source.index('if action=="apply" {_ = NativeMutationGate()}'):source.index('guard let script = notesScript(source + bridge)')]
    assert 'ICloudNotesModelFolderRole(probe)' in apply and 'notesModelRoleMatches(role' in apply
    assert '"mutation_dispatched":false' in apply
    main='''import Foundation\nimport CoreFoundation\n'''+code+'''
let valid:[String:Any] = ["protocol_version":1,"folder_id":"folder","source_id":"source","role":"ordinary_non_system","data_mutated":false,"backend":"notes-shared-folder-role-v1","ancestor_count":0]
precondition(notesModelRoleMatches(valid,folder:"folder",source:"source"))
for (key,value) in [("protocol_version",2 as Any),("protocol_version",true as Any),("folder_id","other" as Any),("source_id","other" as Any),("role","system_trash" as Any),("data_mutated",true as Any),("data_mutated",0 as Any),("backend","other" as Any),("ancestor_count",32 as Any),("ancestor_count",false as Any)] {
 var bad=valid;bad[key]=value;precondition(!notesModelRoleMatches(bad,folder:"folder",source:"source"))
}
print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='notes-folder-guard-') as directory:
        path=Path(directory)/'main.swift';path.write_text(main);binary=Path(directory)/'check'
        built=subprocess.run(['/usr/bin/swiftc',str(path),'-o',str(binary)],capture_output=True,text=True,timeout=60)
        assert built.returncode==0,built.stderr
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0 and result.stdout.strip()=='passed',result.stderr
    print(json.dumps({'status':'passed','synthetic_only':True,'no_notes_access':True}))

if __name__=='__main__':run()
