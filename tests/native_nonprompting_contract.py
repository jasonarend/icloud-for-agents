"""Compile shipped OSA sender against a synthetic send API; never access Notes."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();code=source[source.index('var notesEventSendCount='):source.index('func notesPermission(')].replace('AESendMessage(','syntheticSend(')
    header='''import Foundation
import OSAKit
import Carbon
var sentMode:AESendMode=0
var sentTicks=0
var resultCode:OSStatus=0
func syntheticSend(_ event:UnsafePointer<AppleEvent>?,_ reply:UnsafeMutablePointer<AppleEvent>?,_ mode:AESendMode,_ ticks:Int)->OSStatus {
 sentMode=mode;sentTicks=ticks;return resultCode
}
'''
    tail='''
var event=AppleEvent(descriptorType:typeNull,dataHandle:nil)
var reply=AppleEvent(descriptorType:typeNull,dataHandle:nil)
for ticks:Int32 in [-1,0,20,1000] {
 let code=notesSendWithoutConsent(&event,&reply,AESendMode(kAEWaitReply),0,ticks,nil,nil,nil)
 precondition(code==0)
 precondition((sentMode & AESendMode(kAEDoNotPromptForUserConsent)) != 0)
 precondition(sentTicks==(ticks<=0 ? 300 : min(Int(ticks),300)))
}
for denied:OSStatus in [-1743,-1744,-1712] {
 resultCode=denied
 precondition(notesSendWithoutConsent(&event,&reply,AESendMode(kAEWaitReply),0,20,nil,nil,nil)==OSErr(denied))
}
precondition(notesEventSendCount==7)
guard let script=notesScript("1+1") else {fatalError("Sender install failed")}
var error:NSDictionary?
precondition(script.executeAndReturnError(&error)?.int32Value==2)
print("passed")
'''
    with tempfile.TemporaryDirectory(prefix='native-nonprompting-contract-') as directory:
        file=Path(directory)/'main.swift';file.write_text(header+code+tail);binary=Path(directory)/'contract'
        build=subprocess.run(['/usr/bin/swiftc',str(file),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert build.returncode==0,build.stderr
        result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10);assert result.returncode==0,result.stderr
        assert result.stdout.strip()=='passed'
    return {'status':'passed','real_apple_event_send':False,'notes_access':False,'cases':['every_send_has_do_not_prompt_flag','event_timeout_bounded','denial_and_timeout_preserved','fresh_osa_language_instance','installed_sender_executes_constant_script']}

if __name__=='__main__':
    result=run();path=ROOT/'build/reports/NATIVE-NONPROMPTING-CONTRACT.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
