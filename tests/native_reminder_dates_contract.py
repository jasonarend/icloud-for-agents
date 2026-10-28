"""Exercise shipped date filtering on in-memory Foundation date components."""
import json
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]


def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    start=source.index('func dueDay(_ reminder: EKReminder)')
    function=source[start:source.index('let predicate = store.predicateForReminders(in: selected)',start)]
    completion=source[source.index('func completionInRange('):source.index('guard !selected.isEmpty else {')]
    fixture='''
struct EKReminder {
 var dueDateComponents: DateComponents?
 var calendarItemIdentifier:String=""
 var title:String?=nil
 var notes:String?=nil
 var isCompleted=false
 var priority=0
 var completionDate:Date?=nil
 var lastModifiedDate:Date?=nil
}
var zone=TimeZone(identifier:"America/Chicago")
func components(_ year:Int,_ month:Int,_ day:Int,_ hour:Int?=nil,_ minute:Int?=nil,_ timezone:String?=nil)->DateComponents {
    var d=DateComponents();d.calendar=Calendar(identifier:.gregorian)
    d.year=year;d.month=month;d.day=day;d.hour=hour;d.minute=minute
    if let timezone=timezone { d.timeZone=TimeZone(identifier:timezone) }
    return d
}
'''
    filter_start=source.index('    let filtered = reminders.filter { r in',start)
    filter_code=source[filter_start:source.index('    if nativeFlag != nil || nativeTag != nil || nativeUrgent != nil || nativeAssignment != nil || nativeAttachment != nil {',filter_start)]
    filter_fixture='''
var completionFrom:Date?=nil
var completionBefore:Date?=nil
var modifiedFrom:Date?=nil
var modifiedBefore:Date?=nil
var req:[String:Any]=[:]
var dueFilter:String?=nil
var dueFrom:String?="2026-10-27"
var dueThrough:String?="2026-10-28"
var priorities:[Int]?=[1]
var completed:Bool?=false
var text:String?=nil
let reminders=[
 EKReminder(dueDateComponents:components(2026,10,26),calendarItemIdentifier:"a",title:"Early",notes:"",priority:0),
 EKReminder(dueDateComponents:components(2026,10,27),calendarItemIdentifier:"b",title:"Today",notes:"",priority:1,lastModifiedDate:Date(timeIntervalSince1970:100)),
 EKReminder(dueDateComponents:components(2026,10,29),calendarItemIdentifier:"c",title:"Later",notes:"Synthetic body",priority:5),
 EKReminder(dueDateComponents:nil,calendarItemIdentifier:"undated",title:"Undated",notes:"",priority:9),
 EKReminder(dueDateComponents:components(2026,10,27),calendarItemIdentifier:"done",title:"Done",notes:"",isCompleted:true,priority:1,completionDate:Date(timeIntervalSince1970:100))]
'''
    filter_assertions='''
zone=TimeZone(identifier:"America/Chicago")
precondition(filteredIDs()==["b"])
dueFrom=nil;dueThrough="2026-10-26";priorities=nil
precondition(filteredIDs()==["a"])
dueThrough=nil
precondition(filteredIDs()==["a","b","c","undated"])
dueFilter="2026-10-27";req["include_overdue"]=true
precondition(filteredIDs()==["a","b"])
req["include_overdue"]=false
precondition(filteredIDs()==["b"])
dueFilter=nil;text="BODY"
precondition(filteredIDs()==["c"])
text=nil;completed=nil;completionFrom=Date(timeIntervalSince1970:100);completionBefore=Date(timeIntervalSince1970:200)
precondition(filteredIDs()==["done"])
precondition(completionInRange(true,Date(timeIntervalSince1970:100),completionFrom,completionBefore))
precondition(!completionInRange(true,Date(timeIntervalSince1970:200),completionFrom,completionBefore))
precondition(!completionInRange(false,Date(timeIntervalSince1970:150),completionFrom,completionBefore))
precondition(!completionInRange(true,nil,completionFrom,completionBefore))
completionFrom=nil;completionBefore=nil;modifiedFrom=Date(timeIntervalSince1970:100);modifiedBefore=Date(timeIntervalSince1970:200)
precondition(filteredIDs()==["b"])
precondition(modificationInRange(Date(timeIntervalSince1970:100),modifiedFrom,modifiedBefore))
precondition(!modificationInRange(Date(timeIntervalSince1970:200),modifiedFrom,modifiedBefore))
precondition(!modificationInRange(nil,modifiedFrom,modifiedBefore))
'''
    assertions=''' 
precondition(dueDay(EKReminder(dueDateComponents:nil))==nil)
precondition(dueDay(EKReminder(dueDateComponents:components(2026,3,8)))=="2026-03-08")
precondition(dueDay(EKReminder(dueDateComponents:components(2026,10,27,0,30,"Asia/Tokyo")))=="2026-10-26")
precondition(dueDay(EKReminder(dueDateComponents:components(2026,3,8,1,59,"America/Chicago")))=="2026-03-08")
precondition(dueDay(EKReminder(dueDateComponents:components(2026,3,8,3,0,"America/Chicago")))=="2026-03-08")
zone=TimeZone(identifier:"Pacific/Honolulu")
precondition(dueDay(EKReminder(dueDateComponents:components(2026,1,1,0,30,"UTC")))=="2025-12-31")
precondition(dueDay(EKReminder(dueDateComponents:components(2026,1,1)))=="2026-01-01")
print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='icloud-date-contract-') as directory:
        script=Path(directory)/'dates.swift';binary=Path(directory)/'dates'
        script.write_text('import Foundation\n'+fixture+completion+function+filter_fixture+'\nfunc filteredIDs()->[String] {\n'+filter_code+'return filtered.map { $0.calendarItemIdentifier }\n}\n'+assertions+filter_assertions)
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,check=True,timeout=60)
        result=subprocess.run([str(binary)],capture_output=True,text=True,check=True,timeout=10)
        assert result.stdout.strip()=='passed'
    return {'status':'passed','in_memory_only':True,'checks':['undated','date_only_travel','timed_timezone_day_boundary','DST_transition','year_boundary','inclusive_range','priority_and_completion','undated_exclusion','overdue','case_insensitive_search','completion_utc_inclusive_exclusive_and_unknown','modified_utc_inclusive_exclusive_and_unknown']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-REMINDER-DATES-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
