"""Exercise the shipped Swift graph/public-membership join with synthetic providers."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ID='11111111-1111-4111-8111-111111111111';CHILD='22222222-2222-4222-8222-222222222222';LIST='33333333-3333-4333-8333-333333333333';SOURCE='44444444-4444-4444-8444-444444444444'
STUB='''import Foundation
final class Source {var sourceIdentifier:String;init(_ id:String){sourceIdentifier=id}}
final class Calendar {var calendarIdentifier:String;var source:Source;init(_ id:String,_ sourceID:String){calendarIdentifier=id;source=Source(sourceID)}}
final class EKReminder {var calendar:Calendar;var calendarItemIdentifier:String;var title:String?;var isCompleted:Bool
init(_ value:[String:Any]){calendar=Calendar(value["list"] as! String,value["source"] as! String);calendarItemIdentifier=value["id"] as! String;title=value["title"] as? String;isCompleted=value["completed"] as? Bool ?? false}}
final class Store {func predicateForReminders(in values:[Calendar])->Bool{return true}
func fetchReminders(matching value:Bool, completion:([EKReminder]?)->Void){completion(publicRows)}}
func respond(_ value:[String:Any])->Never{FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:value));exit(0)}
func fail(_ category:String)->Never{respond(["error":category,"native_data_changed":false])}
let input=try! JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
let req:[String:Any]=["list_id":input["requested_list"] as! String]
let action=input["action"] as? String ?? "list_hierarchy",lists=[Calendar(input["list"] as! String,input["source"] as! String)],store=Store()
let publicRows=(input["public"] as! [[String:Any]]).map(EKReminder.init)
func ICloudRemindersListHierarchy(_ payload:Data)->Data{return try! JSONSerialization.data(withJSONObject:input["native"]!)}
func ICloudRemindersListOrder(_ payload:Data)->Data{return ICloudRemindersListHierarchy(payload)}
'''

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();block=source[source.index('if action == "list_hierarchy" || action == "list_order" {'):source.index('if action == "list_sections" ||')]
    with tempfile.TemporaryDirectory(prefix='native-list-graph-') as directory:
        script=Path(directory)/'graph.swift';binary=Path(directory)/'graph';script.write_text(STUB+block)
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],check=True,capture_output=True,timeout=60)
        def request(native_ids=(ID,CHILD),public_ids=(ID,CHILD),**extra):
            data={'requested_list':LIST,'list':LIST,'source':SOURCE,'native':{'protocol_version':1,'source_id':SOURCE,'list_id':LIST,'backend':'reminderkit-list-hierarchy-v1','data_mutated':False,'nodes':[{'id':v,'parent_id':None if v==ID else ID} for v in native_ids]},'public':[{'id':v,'list':LIST,'source':SOURCE,'title':'Synthetic','completed':False} for v in public_ids]};data.update(extra);return data
        def call(data):return json.loads(subprocess.run([str(binary)],input=json.dumps(data),text=True,capture_output=True,check=True,timeout=5).stdout)
        good=call(request());assert good['membership_verified'] and good['hierarchy_complete'] and len(good['nodes'])==2
        assert call(request((),()))['nodes']==[]
        ordered=request(action='list_order');ordered['native'].update(backend='reminderkit-list-order-v1',ordered_ids=[CHILD,ID]);assert call(ordered)['ordered_ids']==[CHILD,ID]
        for invalid in ([ID,ID],[ID],['foreign',CHILD]):
            ordered['native']['ordered_ids']=invalid;assert 'error' in call(ordered)
        for native,public in [((ID,),(ID,CHILD)),((ID,ID),(ID,CHILD)),((ID,CHILD),(ID,ID)),((ID,CHILD),(ID,'not-uuid'))]:assert 'error' in call(request(native,public))
        for change in ({'source':'foreign'},{'list':'foreign'},{'title':None},{'title':'x'*1001}):
            data=request();data['public'][0].update(change);assert 'error' in call(data)
        assert 'error' in call(request(requested_list='unselected'))
    return {'status':'passed','synthetic_only':True,'apple_store_opened':False,'exact_cross_provider_membership_verified':True,'missing_duplicate_foreign_and_unbounded_refused':True}
if __name__=='__main__':print(json.dumps(run()))
