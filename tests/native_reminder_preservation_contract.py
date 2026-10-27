"""Production pre-dispatch native snapshot check with synthetic provider observations."""
import json,subprocess,tempfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
FIXTURE='''
struct Source {let sourceIdentifier="selected"}
struct Calendar {let source=Source();let calendarIdentifier="list"}
struct EKReminder {let calendar=Calendar();let calendarItemIdentifier="task"}
var flag=false
var tagName="Project"
var privateReads=0
func fail(_ code:String)->Never {fatalError(code)}
func respond(_ value:[String:Any])->Never {fatalError("unexpected refusal")}
func output(_ input:Data,_ tags:Bool)->Data {
    privateReads+=1
    let identity=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    var value=identity
    value["native_metadata_complete"]=false;value["observation_consistency"]="separate_provider_reads"
    if tags {value["backend"]="reminderkit-tags-v1";value["tags_complete"]=true;value["coverage"]=["tags"];value["tags"]=[["id":"tag","name":tagName,"native_type":0]]}
    else {value["backend"]="reminderkit-flags-v1";value["flagged"]=flag;value["flag_state"]=flag ? 1:0;value["coverage"]=["flagged"]}
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadFlag(_ input:Data)->Data {output(input,false)}
func ICloudRemindersReadTags(_ input:Data)->Data {output(input,true)}
'''
CHECKS='''
let reminder=EKReminder()
precondition(selectedNativeMetadata(reminder,[:])==nil && privateReads==0)
let expected=selectedNativeMetadata(reminder,["include_native_metadata":true,"include_native_tags":true])!
precondition(expected["coverage"] as! [String]==["flagged","tags"])
precondition(nativeSnapshotMatches(reminder,expected))
flag=true;precondition(!nativeSnapshotMatches(reminder,expected));flag=false
 tagName="Externally changed";precondition(!nativeSnapshotMatches(reminder,expected));tagName="Project"
var wrong=expected;wrong["source_id"]="foreign";precondition(!nativeSnapshotMatches(reminder,wrong))
wrong=expected;wrong["list_id"]="foreign";precondition(!nativeSnapshotMatches(reminder,wrong))
wrong=expected;wrong.removeValue(forKey:"tags");precondition(!nativeSnapshotMatches(reminder,wrong))
precondition(nativeSnapshotMatches(reminder,expected))
print("native snapshot drift checks passed; no Apple store opened")
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    functions=source[source.index('func selectedNativeMetadata('):source.index('if action == "get" {')]
    start=source.index('            if let selectedNative=before["native_metadata"]')
    gate=source[start:source.index('            if operation == "set_completed"',start)]
    with tempfile.TemporaryDirectory(prefix='native-preservation-') as directory:
        p=Path(directory);(p/'main.swift').write_text('import Foundation\n'+FIXTURE+functions+CHECKS)
        result=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
        # Feed the production envelope into the production pre-dispatch block.
        from icloud_for_agents.native_protocol import mutation_request
        metadata={'id':'task','source_id':'selected','list_id':'list','flagged':False,'flag_state':0,
                  'tags':[{'id':'tag','name':'Project','native_type':0}],'tags_complete':True,
                  'coverage':['flagged','tags'],'backend':'reminderkit-flags-and-tags-v1',
                  'native_metadata_complete':False,'observation_consistency':'separate_provider_reads'}
        item={'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'new'},
              'before':{'item':{'id':'task'},'native_metadata':metadata}}
        envelope=mutation_request('reminders',item)
        assert envelope['change']['before']['native_metadata']==metadata
        header=FIXTURE.replace('func fail(_ code:String)->Never {fatalError(code)}','func fail(_ code:String)->Never {print("refused:"+code);exit(0)}')
        runner='''
let input=try! JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
let change=input["change"] as! [String:Any]
let before=change["before"] as! [String:Any]
let reminder=EKReminder()
if CommandLine.arguments[1]=="drift" {flag=true}
func check(_ before:[String:Any],_ reminder:EKReminder) {
'''+gate+'''
 print("mutation_admitted")
}
check(before,reminder)
'''
        (p/'gate.swift').write_text('import Foundation\n'+header+functions+runner)
        built=subprocess.run(['/usr/bin/swiftc',str(p/'gate.swift'),'-o',str(p/'gate')],capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
        def run_gate(payload,scenario):
            return subprocess.run([str(p/'gate'),scenario],input=json.dumps(payload),capture_output=True,text=True,check=True,timeout=5).stdout.strip()
        assert run_gate(envelope,'same')=='mutation_admitted'
        assert run_gate(envelope,'drift')=='refused:snapshot_changed'
        malformed=json.loads(json.dumps(envelope));malformed['change']['before']['native_metadata']='invalid'
        assert run_gate(malformed,'same')=='refused:invalid_mutation'
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['default_no_private_reads','complete_combined_snapshot','flag_tag_scope_and_missing_data_drift_refused','real_envelope_to_pre_dispatch_gate']}))
if __name__=='__main__':run()
