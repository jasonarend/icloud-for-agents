"""Actual Swift gate/JXA bridge with synthetic Notes objects, never a native store."""
import json,subprocess,tempfile
from pathlib import Path
from native_write_contract import FIXTURE
ROOT=Path(__file__).resolve().parents[1]

def run():
    swift=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();gate=swift[swift.index('@objc(ICloudAgentsMutationGate)'):swift.index('func respond(')]
    bridge=swift[swift.index('    let encoded = data.base64EncodedString()'):swift.index('    // Keep the application run loop available')]
    # Keep the real production callback/encoding construction, replacing only the
    # fixed provider expression with its synthetic object fixture.
    bridge=bridge.replace("Application('com.apple.Notes')",'app')
    with tempfile.TemporaryDirectory(prefix='native-dispatch-gate-') as directory:
        path=Path(directory);js=path/'fixture.js';js.write_text(FIXTURE+'\n'+(ROOT/'src/icloud_for_agents/native/notes-write.js').read_text())
        program='''import Foundation
import OSAKit
'''+gate+'''
let action="apply"
let data=FileHandle.standardInput.readDataToEndOfFile()
let source=try String(contentsOfFile:CommandLine.arguments[1],encoding:.utf8)
_ = NativeMutationGate()
if CommandLine.arguments[2]=="closed" {precondition(NativeMutationGate.cancel()==false)}
'''+bridge+'''
let script=OSAScript(source:source+bridge,language:OSALanguage(forName:"JavaScript"))
var error:NSDictionary?
guard let text=script.executeAndReturnError(&error)?.stringValue else {fatalError("Synthetic gate bridge failed")}
print(text)
let dispatched=NativeMutationGate.cancel()
precondition(NativeMutationGate.beginMutation()==false)
if CommandLine.arguments[2]=="closed" {precondition(dispatched==false)} else {precondition(dispatched==true)}
'''
        file=path/'main.swift';file.write_text(program);binary=path/'contract';compiled=subprocess.run(['/usr/bin/swiftc',str(file),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert compiled.returncode==0,compiled.stderr
        request={'protocol_version':1,'action':'apply','source_ids':['selected'],'change':{'operation':'create_folder','source_id':'selected','fields':{'title':'New folder'}}}
        for state in ('closed','open'):
            result=subprocess.run([str(binary),str(js),state],input=json.dumps(request),capture_output=True,text=True,timeout=10);assert result.returncode==0,result.stderr;value=json.loads(result.stdout)
            if state=='closed':assert value['mutation_dispatched'] is False and value['category']=='native_dispatch_cancelled'
            else:assert value['result']['id']=='new-folder'
    # Exercise the shipped timeout classifier against the same gate. No native
    # primitive is called; an entered primitive must remain uncertain.
    failure=swift[swift.index('func fail('):swift.index('func dateParts(')]
    with tempfile.TemporaryDirectory(prefix='native-dispatch-timeout-') as directory:
        path=Path(directory);file=path/'main.swift';binary=path/'contract'
        program='import Foundation\nvar requestAction:String?="apply"\nvar requestService:String?=CommandLine.arguments.count>2 ? "reminders" : "notes"\nvar privateReminderGateArmed=CommandLine.arguments.count>2\nvar mutationDispatched=false\n'+gate+"\nfunc respond(_ value:[String:Any])->Never {let bytes=try! JSONSerialization.data(withJSONObject:value);FileHandle.standardOutput.write(bytes);exit(0)}\n"+failure+'\nif CommandLine.arguments[1]=="entered" {precondition(NativeMutationGate.beginMutation())}\nfail("mutation_failed_or_uncertain")\n'
        file.write_text(program);compiled=subprocess.run(['/usr/bin/swiftc',str(file),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert compiled.returncode==0,compiled.stderr
        for state in ('closed','entered'):
            result=subprocess.run([str(binary),state],capture_output=True,text=True,timeout=5);assert result.returncode==0,result.stderr;value=json.loads(result.stdout)
            assert value['mutation_dispatched'] is (state=='entered')
            assert value['error']==('mutation_failed_or_uncertain' if state=='entered' else 'notes_validation_timeout')
            flag=subprocess.run([str(binary),state,'flag'],capture_output=True,text=True,timeout=5);assert flag.returncode==0,flag.stderr;flag_value=json.loads(flag.stdout)
            assert flag_value['mutation_dispatched'] is (state=='entered') and flag_value['error']=='mutation_failed_or_uncertain'
    with tempfile.TemporaryDirectory(prefix='native-dispatch-deadline-') as directory:
        path=Path(directory);file=path/'main.swift';binary=path/'contract'
        clocked=gate.replace('ProcessInfo.processInfo.systemUptime','syntheticUptime')
        file.write_text('import Foundation\nvar syntheticUptime:TimeInterval=100\n'+clocked+'\nNativeMutationGate.armDeadline()\nsyntheticUptime=126\nprecondition(!NativeMutationGate.beginMutation())\nprecondition(!NativeMutationGate.cancel())\nprint("expired gate refused before primitive")\n')
        compiled=subprocess.run(['/usr/bin/swiftc',str(file),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert compiled.returncode==0,compiled.stderr
        subprocess.run([str(binary)],capture_output=True,text=True,check=True,timeout=5)
    return {'status':'passed','real_notes_access':False,'cases':['production_bridge_resolves_gate_class','cancel_before_dispatch_has_no_native_primitive','primitive_entry_recorded_monotonically','closed_gate_prevents_later_mutation','synthetic_folder_creation_only','timeout_before_primitive_proves_no_dispatch','timeout_after_entry_remains_uncertain','monotonic_deadline_refuses_late_primitive_even_if_main_loop_stalls']}

if __name__=='__main__':
    result=run();path=ROOT/'build/reports/NATIVE-DISPATCH-GATE-CONTRACT.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
