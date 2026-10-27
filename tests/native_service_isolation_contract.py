"""Instrument the shipped helper to detect any disabled Reminders status query.

Runs OS Notes permission status only, never a note/account/event-store content read.
The substituted Reminders probe exits with a marker instead of querying EventKit.
"""
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]


def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    needle='EKEventStore.authorizationStatus(for: .reminder)'
    assert source.count(needle)==1
    source=source.replace(needle,'unexpectedRemindersProbe()')
    source=source.replace('import AppKit\n','''import AppKit
func unexpectedRemindersProbe()->EKAuthorizationStatus {
 FileHandle.standardOutput.write(Data("{\\"probe_invoked\\":true}".utf8));exit(71)
}
''',1)
    with tempfile.TemporaryDirectory(prefix='icloud-service-isolation-') as directory:
        script=Path(directory)/'main.swift';script.write_text(source);binary=Path(directory)/'helper'
        info=Path(directory)/'Info.plist';info.write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.jasonarend.icloud-for-agents.native',
            'NSAppleEventsUsageDescription':'Read-only permission-status isolation test; no native content access.'}))
        native=ROOT/'src/icloud_for_agents/native';model=Path(directory)/'notes-model.o'
        subprocess.run(['/usr/bin/clang','-fobjc-arc','-c',str(native/'notes-model.m'),'-o',str(model)],capture_output=True,check=True,timeout=60)
        subprocess.run(['/usr/bin/swiftc',str(script),str(model),'-import-objc-header',str(native/'notes-model.h'),'-framework','CoreData','-o',str(binary),'-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist','-Xlinker',str(info)],capture_output=True,check=True,timeout=60)
        def call(service):
            request={'protocol_version':1,'service':service,'action':'status'}
            return subprocess.run([str(binary)],input=json.dumps(request),capture_output=True,text=True,timeout=10)
        notes=call('notes');assert notes.returncode==0,notes.stderr
        assert json.loads(notes.stdout)['service']=='notes'
        invalid=call('unknown');assert invalid.returncode==0
        assert json.loads(invalid.stdout)['error']=='unsupported_service'
        reminders=call('reminders');assert reminders.returncode==71
        assert json.loads(reminders.stdout)['probe_invoked'] is True
    return {'status':'passed','notes_content_access':False,'reminders_store_access':False,
            'checks':['notes_status_never_queries_reminders_permission','unknown_service_refused','probe_instrumentation_active']}


if __name__=='__main__':
    result=run();(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/NATIVE-SERVICE-ISOLATION-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
