"""Actual lifecycle block with synthetic AppKit classes; never launches an app."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();block=source[source.index('    // Reuse the running fixed target'):source.index('    if action == "diagnose_read_access"')]
    fixture=r'''
import Foundation
let scenario=CommandLine.arguments[1]
let action=scenario=="status" ? "status":"get"
var launches=0
class NSApplication {static let shared=NSApplication()}
class NSRunningApplication {
 var isTerminated=false
 static func runningApplications(withBundleIdentifier id:String)->[NSRunningApplication]{precondition(id=="com.apple.Notes");return scenario=="running" ? [NSRunningApplication()]:[]}
}
class NSWorkspace {
 static let shared=NSWorkspace()
 class OpenConfiguration {var activates=true;var addsToRecentItems=true}
 func openApplication(at url:URL,configuration:OpenConfiguration,completionHandler:(NSRunningApplication?,Error?)->Void){
  precondition(url.path=="/System/Applications/Notes.app" && !configuration.activates && !configuration.addsToRecentItems)
  launches+=1
  completionHandler(scenario=="fail" ? nil:NSRunningApplication(),nil)
 }
}
func fail(_ error:String)->Never {print("failed:"+error+":"+String(launches));exit(0)}
'''
    with tempfile.TemporaryDirectory(prefix='notes-launch-contract-') as directory:
        p=Path(directory);(p/'main.swift').write_text(fixture+block+'\nprint("ready:"+String(launches))\n')
        compiled=subprocess.run(['/usr/bin/swiftc',str(p/'main.swift'),'-o',str(p/'contract')],capture_output=True,text=True,timeout=60);assert compiled.returncode==0,compiled.stderr
        for scenario,expected in [('status','ready:0'),('running','ready:0'),('cold','ready:1'),('fail','failed:notes_application_launch_failed:1')]:
            value=subprocess.run([str(p/'contract'),scenario],capture_output=True,text=True,check=True,timeout=5);assert value.stdout.strip()==expected,(scenario,value.stdout)
    report={'status':'passed','synthetic_AppKit_only':True,'checks':['status_does_not_launch','running_target_no_reopen','cold_target_fixed_background_launch','failed_launch_stops_without_retry']};print(json.dumps(report));return report
if __name__=='__main__':run()
