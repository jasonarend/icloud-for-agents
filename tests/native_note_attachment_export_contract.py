"""Shipped attachment export JXA and bounded native-file reader, without Apple data."""
import json,subprocess,tempfile
from pathlib import Path
from native_script_contract import FIXTURE,ROOT
JS=r'''
let exportCalls=0,changed=false;
function run(){const req=readRequest();const app=fixtureApplication('Notes');app.save=(asset,options)=>{if(asset.id()!=='attachment-1' || !options.in)throw Error('wrong_export');exportCalls++;if(req.synthetic_change)fixtureNotes[0].modificationDate=()=>new Date();};
try {const value=JSON.parse(readNotes(app,req,req.synthetic_no_internal_path?undefined:'/synthetic/private/asset'));return JSON.stringify({value:value,calls:exportCalls});}catch(e){return JSON.stringify({error:String(e),calls:exportCalls});}}
'''
def run():
 source=(ROOT/'src/icloud_for_agents/native/notes.js').read_text();swift=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text();function=swift[swift.index('func notesExportBytes('):swift.index('let data = requestData()')]
 with tempfile.TemporaryDirectory(prefix='notes-attachment-export-') as directory:
  p=Path(directory);js=p/'fixture.js';js.write_text(FIXTURE+'\n'+source+'\n'+JS)
  def call(**fields):
   request={'protocol_version':1,'action':'attachment_export','source_ids':['selected'],'folder_id':'folder-1','id':'note-1','attachment_id':'attachment-1','max_bytes':64,**fields};r=subprocess.run(['/usr/bin/osascript','-l','JavaScript',str(js)],input=json.dumps(request),capture_output=True,text=True,timeout=10);assert r.returncode==0,r.stderr;return json.loads(r.stdout)
  good=call();assert good['calls']==1 and good['value']['attachment_id']=='attachment-1' and good['value']['data_mutated'] is False
  for fields in ({'attachment_id':'foreign'},{'folder_id':'folder-child'},{'id':'note-2'},{'max_bytes':0},{'synthetic_no_internal_path':True,'path':'/caller/controlled'}):
   result=call(**fields);assert 'error' in result and result['calls']==0,result
  changed=call(synthetic_change=True);assert 'snapshot_changed' in changed['error'] and changed['calls']==1
  main=p/'main.swift';binary=p/'contract';main.write_text('import Foundation\nimport Darwin\n'+function+'''
let root=URL(fileURLWithPath:CommandLine.arguments[1]);let good=root.appendingPathComponent("good");try! Data("synthetic".utf8).write(to:good)
precondition(notesExportBytes(good,9)==Data("synthetic".utf8));precondition(notesExportBytes(good,8)==nil)
let link=root.appendingPathComponent("symlink");try! FileManager.default.createSymbolicLink(at:link,withDestinationURL:good);precondition(notesExportBytes(link,100)==nil)
let hard=root.appendingPathComponent("hardlink");precondition(Darwin.link(good.path,hard.path)==0);precondition(notesExportBytes(hard,100)==nil);precondition(notesExportBytes(good,100)==nil)
let fifo=root.appendingPathComponent("fifo");precondition(mkfifo(fifo.path,0o600)==0);precondition(notesExportBytes(fifo,100)==nil);precondition(notesExportBytes(root,100)==nil)
print("bounded regular-file bytes only")
''');r=subprocess.run(['/usr/bin/swiftc',str(main),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert r.returncode==0,r.stderr;r=subprocess.run([str(binary),str(p)],capture_output=True,text=True,timeout=5);assert r.returncode==0,r.stderr
 return {'status':'passed','apple_data_opened':False,'exact_scoped_script_export':True,'locked_foreign_changed_and_caller_paths_refused':True,'oversize_symlink_hardlink_fifo_and_directory_refused':True}
if __name__=='__main__':print(json.dumps(run()))
