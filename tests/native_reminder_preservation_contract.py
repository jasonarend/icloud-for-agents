"""Production pre-dispatch native snapshot check with synthetic provider observations."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
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
    with tempfile.TemporaryDirectory(prefix='native-preservation-') as directory:
        p=Path(directory);(p/'main.swift').write_text('import Foundation\n'+FIXTURE+functions+CHECKS)
        result=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(p/'contract')],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr
    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['default_no_private_reads','complete_combined_snapshot','flag_tag_scope_and_missing_data_drift_refused']}))
if __name__=='__main__':run()
