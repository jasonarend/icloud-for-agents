"""Compile shipped Notes preflight against a synthetic Apple-event permission API."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();function=source[source.index('func notesPermission('):source.index('func fail(')]
    needle='NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Notes").contains(where: { !$0.isTerminated })'
    assert needle in function;function=function.replace(needle,'testRunning').replace('AEDeterminePermissionToAutomateTarget(','syntheticPermission(').replace('prompt ? 60 : 5','testDeadline')
    header='''import Foundation
import AppKit
import ApplicationServices
let ipcDirectory:URL?=nil
var testRunning=true
var testDeadline=0.1
var testCode:OSStatus=0
var testSlow=false
var calls=0
var lastPrompt=false
func syntheticPermission(_ target:UnsafePointer<AEAddressDesc>!,_ eventClass:AEEventClass,_ eventID:AEEventID,_ prompt:Bool)->OSStatus {
 precondition(!Thread.isMainThread)
 precondition(target.pointee.descriptorType==typeApplicationBundleID)
 precondition(eventClass==typeWildCard && eventID==typeWildCard)
 calls+=1;lastPrompt=prompt
 if testSlow { Thread.sleep(forTimeInterval:0.5) }
 return testCode
}
'''
    tail='''
testRunning=false
precondition(notesPermission(prompt:false) == -600 && calls==0)
testRunning=true
for code:OSStatus in [0,-1743,-1744,-600] {
 testCode=code
 precondition(notesPermission(prompt:false)==code && !lastPrompt)
}
testCode=0
precondition(notesPermission(prompt:true)==0 && lastPrompt)
testSlow=true
precondition(notesPermission(prompt:false) == -1712)
print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='native-permission-contract-') as directory:
        path=Path(directory)/'main.swift';path.write_text(header+function+tail);binary=Path(directory)/'contract'
        subprocess.run(['/usr/bin/swiftc',str(path),'-o',str(binary)],check=True,capture_output=True,timeout=60)
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10);assert result.returncode==0,result.stderr;assert result.stdout.strip()=='passed'
    return {'status':'passed','real_permission_api_called':False,'native_data_access':False,'cases':['stable_bundle_address','off_main_permission_api','nonrunning_target_no_call','grant_deny_consent_error_propagation','explicit_prompt_only','bounded_timeout']}

if __name__=='__main__':
    result=run();path=ROOT/'build/reports/NATIVE-PERMISSION-CONTRACT.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
