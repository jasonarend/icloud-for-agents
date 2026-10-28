"""Fixed native flag filtering over a bounded public page, with no Apple store."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIXTURE='''
import Foundation
import CoreFoundation
struct EKReminder {let calendarItemIdentifier:String;let flagged:Bool;let tags:[String]}
let filtered=[EKReminder(calendarItemIdentifier:"a",flagged:false,tags:[]),EKReminder(calendarItemIdentifier:"b",flagged:true,tags:["Project"]),EKReminder(calendarItemIdentifier:"c",flagged:false,tags:["Project"])]
let scenario=CommandLine.arguments[1]
let offset=Int(CommandLine.arguments[2])!
let limit=1
var req:[String:Any]=["flagged":scenario=="false" ? false : true]
if scenario=="invalid" {req["flagged"]=1}
if scenario=="tag" || scenario=="both" || scenario=="case" {req["tag"]=scenario=="case" ? "project" : "Project"}
if scenario=="tag" || scenario=="case" {req.removeValue(forKey:"flagged")}
if scenario=="unfiltered" {req.removeValue(forKey:"flagged")}
if scenario=="urgent" || scenario=="urgent_false" {req.removeValue(forKey:"flagged");req["urgent"]=scenario=="urgent"}
if scenario=="assigned" || scenario=="unassigned" || scenario=="both_new" {req.removeValue(forKey:"flagged");req["has_current_assignment"]=scenario != "unassigned"}
let nativeFlag=(scenario=="attached" || scenario=="unattached" || scenario=="both_new") ? nil : req["flagged"]
let nativeUrgent=req["urgent"]
if scenario=="attached" || scenario=="unattached" || scenario=="both_new" {req.removeValue(forKey:"flagged");req["has_native_attachments"]=scenario != "unattached"}
let nativeAttachment=req["has_native_attachments"]
let nativeAssignment=req["has_current_assignment"]
let nativeTag=req["tag"]
func object(_ reminder:EKReminder)->[String:Any] {return ["id":reminder.calendarItemIdentifier]}
func selectedNativeMetadata(_ reminder:EKReminder,_ options:[String:Any])->[String:Any]? {
 if scenario=="unavailable" {return nil}
 return ["id":reminder.calendarItemIdentifier,"flagged":reminder.flagged,"urgent":reminder.flagged,"tags":reminder.tags.map { ["name":$0] },"tags_complete":true,"current_assignment_reported":reminder.flagged,"attachments":reminder.flagged ? [["id":"attachment"]] : [],"attachment_metadata_complete":false]
}
func fail(_ code:String)->Never {print("error:"+code);exit(0)}
func respond(_ value:[String:Any])->Never {let data=try! JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!);exit(0)}
'''
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();start=source.index('    if nativeFlag != nil || nativeTag != nil || nativeUrgent != nil || nativeAssignment != nil || nativeAttachment != nil {',source.index('    let filtered = reminders.filter'))
    code=source[start:source.index('    let page = Array(filtered.dropFirst(offset)',start)]
    with tempfile.TemporaryDirectory(prefix='reminder-flag-filter-') as directory:
        p=Path(directory);(p/'main.swift').write_text(FIXTURE+'\nlet completionEvidence:[String:Any]?=nil\nlet modificationEvidence:[String:Any]?=nil\n'+code+'\nprint("missing filter")\n')
        compiled=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert compiled.returncode==0,compiled.stderr
        def call(scenario,offset):return subprocess.run([str(p/'contract'),scenario,str(offset)],capture_output=True,text=True,check=True,timeout=5).stdout.strip()
        first=json.loads(call('true',0));assert first['items']==[] and first['next_offset']==1 and first['done'] is False and first['native_flag_filter']['scanned']==1
        second=json.loads(call('true',1));assert second['items'][0]['id']=='b' and second['items'][0]['native_metadata']['flagged'] is True and second['next_offset']==2 and second['done'] is False
        last=json.loads(call('false',2));assert last['items'][0]['id']=='c' and last['done'] is True and last['next_offset']==3
        assert call('unavailable',0)=='error:private_metadata_unavailable'
        attached=json.loads(call('attached',1));assert attached['items'][0]['id']=='b' and attached['native_attachment_filter']['matched']==1
        assert json.loads(call('unattached',2))['items'][0]['id']=='c'
        both_new=json.loads(call('both_new',1));assert both_new['items'][0]['id']=='b' and both_new['native_assignment_filter']==both_new['native_attachment_filter']
        assigned=json.loads(call('assigned',1));assert assigned['items'][0]['id']=='b' and assigned['native_assignment_filter']['matched']==1
        assert json.loads(call('unassigned',2))['items'][0]['id']=='c'
        urgent=json.loads(call('urgent',1));assert urgent['items'][0]['id']=='b' and urgent['native_urgent_filter']['matched']==1
        assert json.loads(call('urgent_false',2))['items'][0]['id']=='c'
        tagged=json.loads(call('tag',1));assert tagged['items'][0]['id']=='b' and tagged['native_tag_filter']['matched']==1
        both=json.loads(call('both',1));assert both['items'][0]['id']=='b' and both['native_flag_filter']==both['native_tag_filter']
        assert json.loads(call('both',2))['items']==[] and json.loads(call('case',1))['items']==[]
        empty_start=source.index('if let urgent=nativeUrgent {')
        empty=source[empty_start:source.index('let dueFilter = req["due_date"]',empty_start)]
        (p/'empty.swift').write_text(FIXTURE+'\nlet selected:[Int]=[]\nlet requestedList:String?=nil\n'+empty+'\nprint("missing empty result")\n')
        built=subprocess.run(['/usr/bin/swiftc',str(p/'empty.swift'),'-o',str(p/'empty')],capture_output=True,text=True,timeout=60);assert built.returncode==0,built.stderr
        empty_result=subprocess.run([str(p/'empty'),'true','0'],capture_output=True,text=True,check=True,timeout=5)
        value=json.loads(empty_result.stdout);assert value['items']==[] and value['done'] is True and value['next_offset']==0 and value['native_flag_filter']['scanned']==0
        assert subprocess.run([str(p/'empty'),'invalid','0'],capture_output=True,text=True,check=True,timeout=5).stdout.strip()=='error:invalid_request'
        empty_both=json.loads(subprocess.run([str(p/'empty'),'both_new','0'],capture_output=True,text=True,check=True,timeout=5).stdout);assert empty_both['native_assignment_filter']==empty_both['native_attachment_filter'] and empty_both['done'] is True
        empty_tag=json.loads(subprocess.run([str(p/'empty'),'both','0'],capture_output=True,text=True,check=True,timeout=5).stdout);assert empty_tag['native_flag_filter']==empty_tag['native_tag_filter']
        unfiltered=json.loads(subprocess.run([str(p/'empty'),'unfiltered','7'],capture_output=True,text=True,check=True,timeout=5).stdout)
        assert unfiltered=={'protocol_version':1,'items':[],'done':True,'next_offset':7,'pagination_consistency':'live_offset_may_shift'}

    print(json.dumps({'status':'passed','apple_store_opened':False,'checks':['bounded_scan','empty_page_not_done','empty_source_has_filter_evidence','true_false_matching','invalid_type_and_unavailable_metadata_refused']}))
if __name__=='__main__':run()
