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
var urgent=false
var tagName="Project"
var childIDs=["synthetic-child"]
var earlyFingerprint:Any=NSNull()
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
func ICloudRemindersReadUrgent(_ input:Data)->Data {
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any];value["coverage"]=["urgent"];value["urgent"]=urgent
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadFlag(_ input:Data)->Data {output(input,false)}
func ICloudRemindersReadAssignment(_ input:Data)->Data {
    privateReads+=1
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    value["backend"]="reminderkit-assignment-v1";value["coverage"]=["assignment"];value["current_assignment_reported"]=false;value["assignment_record_count"]=0;value["assignment_metadata_complete"]=false;value["native_metadata_complete"]=false;value["observation_consistency"]="separate_provider_reads"
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadEarly(_ input:Data)->Data {
    privateReads+=1
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    value["backend"]="reminderkit-early-v1";value["coverage"]=["early"];value["early_alerts"]=[];value["early_payload_fingerprint"]=earlyFingerprint;value["native_metadata_complete"]=false;value["observation_consistency"]="separate_provider_reads"
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadSection(_ input:Data)->Data {
    privateReads+=1
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    value["backend"]="reminderkit-section-v1";value["coverage"]=["section"];value["section"]=NSNull();value["section_status"]="provider_not_reported";value["native_metadata_complete"]=false;value["observation_consistency"]="separate_provider_reads"
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadTags(_ input:Data)->Data {output(input,true)}
func ICloudRemindersReadChildren(_ input:Data)->Data {
    privateReads+=1
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    value["backend"]="reminderkit-children-v1";value["coverage"]=["children"];value["native_metadata_complete"]=false
    value["observation_consistency"]="separate_provider_reads";value["child_ids"]=childIDs;value["children_complete"]=false;value["children_fetch_status"]="provider_reported"
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
func ICloudRemindersReadHierarchy(_ input:Data)->Data {
    privateReads+=1
    var value=try! JSONSerialization.jsonObject(with:input) as! [String:Any]
    value["backend"]="reminderkit-hierarchy-v1";value["coverage"]=["hierarchy"];value["native_metadata_complete"]=false
    value["observation_consistency"]="separate_provider_reads";value["is_subtask"]=false;value["parent_id"]=NSNull();value["has_subtasks"]=false;value["hierarchy_complete"]=false;value["children_status"]="provider_reported"
    return try! JSONSerialization.data(withJSONObject:["native_metadata":value])
}
'''
CHECKS='''
let reminder=EKReminder()
precondition(selectedNativeMetadata(reminder,[:])==nil && privateReads==0)
let expected=selectedNativeMetadata(reminder,["include_native_metadata":true,"include_native_tags":true,"include_native_hierarchy":true,"include_native_children":true,"include_native_section":true,"include_native_early":true])!
precondition(expected["coverage"] as! [String]==["flagged","tags","hierarchy","children","section","early"])
precondition(nativeSnapshotMatches(reminder,expected))
let combined=selectedNativeMetadata(reminder,["include_native_metadata":true,"include_native_tags":true,"include_native_hierarchy":true])!
precondition(combined["coverage"] as! [String]==["flagged","tags","hierarchy"] && combined["backend"] as! String=="reminderkit-combined-v1" && combined["hierarchy_complete"] as! Bool==false)
flag=true;precondition(!nativeSnapshotMatches(reminder,expected));flag=false
 tagName="Externally changed";precondition(!nativeSnapshotMatches(reminder,expected));tagName="Project"
earlyFingerprint=String(repeating:"a",count:64);precondition(!nativeSnapshotMatches(reminder,expected));earlyFingerprint=NSNull()
childIDs.append("new-child");precondition(!nativeSnapshotMatches(reminder,expected));childIDs.removeLast()
var wrong=expected;wrong["source_id"]="foreign";precondition(!nativeSnapshotMatches(reminder,wrong))
wrong=expected;wrong["list_id"]="foreign";precondition(!nativeSnapshotMatches(reminder,wrong))
wrong=expected;wrong.removeValue(forKey:"tags");precondition(!nativeSnapshotMatches(reminder,wrong))
precondition(nativeSnapshotMatches(reminder,expected))
print("native snapshot drift checks passed; no Apple store opened")
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    move_gate=source[source.index('        if operation == "move_reminder" { fail("unsupported_hierarchy_move") }'):].splitlines()[0]
    assert source.index(move_gate)<source.index('            } else if operation == "move_reminder" {')
    functions=source[source.index('func selectedNativeMetadata('):source.index('if action == "get" {')]
    start=source.index('            if let selectedNative=before["native_metadata"]')
    gate=source[start:source.index('            if operation == "create_subtask"',start)]
    with tempfile.TemporaryDirectory(prefix='native-preservation-') as directory:
        p=Path(directory);(p/'main.swift').write_text('import Foundation\n'+FIXTURE+functions+CHECKS)
        (p/'move.swift').write_text('import Foundation\nlet operation=CommandLine.arguments[1]\nfunc fail(_ code:String)->Never {print("refused:"+code);exit(0)}\n'+move_gate+'\nprint("admitted")\n')
        move_build=subprocess.run(['/usr/bin/swiftc',str(p/'move.swift'),'-o',str(p/'move')],capture_output=True,text=True,timeout=60);assert move_build.returncode==0,move_build.stderr
        for operation,expected in [('move_reminder','refused:unsupported_hierarchy_move'),('update_reminder','admitted')]:
            actual=subprocess.run([str(p/'move'),operation],capture_output=True,text=True,check=True,timeout=5).stdout.strip();assert actual==expected
        result=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
        # Feed the production envelope into the production pre-dispatch block.
        from icloud_for_agents.native_protocol import mutation_request
        metadata={'id':'task','source_id':'selected','list_id':'list','flagged':False,'flag_state':0,
                  'tags':[{'id':'tag','name':'Project','native_type':0}],'tags_complete':True,
                  'coverage':['flagged','tags','hierarchy','children','section','early'],'early_alerts':[],'early_payload_fingerprint':None,'section':None,'section_status':'provider_not_reported','backend':'reminderkit-combined-v1',
                  'is_subtask':False,'parent_id':None,'has_subtasks':False,'hierarchy_complete':False,'children_status':'provider_reported',
                  'child_ids':['synthetic-child'],'children_complete':False,'children_fetch_status':'provider_reported',
                  'native_metadata_complete':False,'observation_consistency':'separate_provider_reads'}
        item={'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'new'},
              'before':{'item':{'id':'task'},'native_metadata':metadata,'urgent':False}}
        envelope=mutation_request('reminders',item)
        assert envelope['change']['before']['native_metadata']==metadata
        header=FIXTURE.replace('func fail(_ code:String)->Never {fatalError(code)}','func fail(_ code:String)->Never {print("refused:"+code);exit(0)}')
        runner='''
let input=try! JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
let change=input["change"] as! [String:Any]
let before=change["before"] as! [String:Any]
let reminder=EKReminder()
let identifier="task",sourceID="selected"
if CommandLine.arguments[1]=="drift" {flag=true}
if CommandLine.arguments[1]=="urgent_drift" {urgent=true}
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
        assert run_gate(envelope,'urgent_drift')=='refused:snapshot_changed'
        missing=json.loads(json.dumps(envelope));del missing['change']['before']['urgent']
        assert run_gate(missing,'same')=='refused:snapshot_changed'
        malformed=json.loads(json.dumps(envelope));malformed['change']['before']['native_metadata']='invalid'
        assert run_gate(malformed,'same')=='refused:invalid_mutation'
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['default_no_private_reads','complete_combined_snapshot','flag_tag_scope_and_missing_data_drift_refused','real_envelope_to_pre_dispatch_gate']}))
if __name__=='__main__':run()
