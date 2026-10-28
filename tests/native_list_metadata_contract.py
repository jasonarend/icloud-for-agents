"""Shipped list membership hashing over synthetic native-object projections."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();functions=source[source.index('// Public EventKit list colors'):source.index('if action == "default_list"')].replace('EKCalendar','FixtureCalendar').replace('EKReminder','FixtureReminder')
    fixture='''import Foundation
import CoreFoundation
import CryptoKit
import AppKit
struct FixtureSource {let sourceIdentifier:String}
struct FixtureCalendar {let calendarIdentifier:String;let title:String;let source:FixtureSource;let allowsContentModifications:Bool;var cgColor:CGColor? {CGColor(red:0.2,green:0.4,blue:0.6,alpha:1)}}
struct FixtureReminder {let calendarItemIdentifier:String}
let list=FixtureCalendar(calendarIdentifier:"synthetic-list",title:"Synthetic",source:FixtureSource(sourceIdentifier:"selected"),allowsContentModifications:true)
'''
    cases='''
let a=FixtureReminder(calendarItemIdentifier:"id/a")
let b=FixtureReminder(calendarItemIdentifier:"id-\\u{1F600}")
let first=membershipRecord([a,b],list)
precondition(NSDictionary(dictionary:first).isEqual(to:membershipRecord([b,a],list)))
precondition(first["sha256"] as? String != membershipRecord([a],list)["sha256"] as? String)
precondition(first["count"] as? Int==2 && first["source_id"] as? String=="selected")
precondition((membershipRecord([],list)["sha256"] as? String)?.count==64)
precondition(listRecord(list)["writable"] as? Bool==true)
precondition(defaultListRecord(list,[list])["state"] as? String=="available")
let outside=defaultListRecord(list,[])
precondition(outside["state"] as? String=="outside_selected_scope" && outside["list"] is NSNull)
precondition(defaultListRecord(nil,[list])["state"] as? String=="unavailable")
let ordinary:[String:Any] = ["grocery_categorization":false,"grocery_shopping_session":false,"is_group":false,"shared":false]
precondition(sectionListFeaturesMatch(ordinary,ordinary))
var changed=ordinary;changed["shared"]=true;precondition(!sectionListFeaturesMatch(changed,ordinary))
var grocery=ordinary;grocery["grocery_categorization"]=true;precondition(!sectionListFeaturesMatch(grocery,grocery))
var invalid=ordinary;invalid["is_group"]=0;precondition(!sectionListFeaturesMatch(invalid,invalid))

print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='native-list-metadata-') as directory:
        script=Path(directory)/'main.swift';script.write_text(fixture+functions+cases);binary=Path(directory)/'contract'
        result=subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert result.returncode==0,result.stderr
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10);assert result.returncode==0,result.stderr;assert result.stdout.strip()=='passed'
    return {'status':'passed','native_store_access':False,'cases':['stable_membership_hash_across_order','changed_membership_detected','source_and_count_bound','unicode_ids','empty_list_proof','list_writability_projection','section_list_feature_drift_and_grocery_refusal']}

if __name__=='__main__':
    result=run();path=ROOT/'build/reports/NATIVE-LIST-METADATA-CONTRACT.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
