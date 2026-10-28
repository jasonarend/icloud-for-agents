"""Execute the production native input reader and parser with synthetic large requests."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    reader=source[source.index('func requestData()'):source.index('var notesEventSendCount=')]
    parser=source[source.index('let data = requestData()'):source.index('responseRequestID = req["request_id"]')]
    with tempfile.TemporaryDirectory(prefix='native-request-bounds-') as directory:
        root=Path(directory);swift=root/'main.swift';binary=root/'contract'
        swift.write_text('import Foundation\nimport Darwin\nlet ipcDirectory:URL? = CommandLine.arguments.count>1 ? URL(fileURLWithPath:CommandLine.arguments[1]) : nil\nfunc fail(_ code:String)->Never {print("refused:"+code);exit(0)}\n'+reader+parser+'print("accepted:"+action)\n')
        build=subprocess.run(['/usr/bin/swiftc',str(swift),'-o',str(binary)],capture_output=True,text=True,timeout=60);assert build.returncode==0,build.stderr
        request=json.dumps({'protocol_version':1,'action':'apply','padding':'x'*80000}).encode()
        result=subprocess.run([str(binary)],input=request,capture_output=True,timeout=10);assert result.returncode==0 and result.stdout==b'accepted:apply\n'
        ipc=root/'ipc';ipc.mkdir();file=ipc/'request.json';file.write_bytes(request);file.chmod(0o600)
        result=subprocess.run([str(binary),str(ipc)],capture_output=True,timeout=10);assert result.returncode==0 and result.stdout==b'accepted:apply\n'
        too_big=json.dumps({'protocol_version':1,'action':'apply','padding':'x'*262145}).encode()
        result=subprocess.run([str(binary)],input=too_big,capture_output=True,timeout=10);assert result.returncode==0 and result.stdout==b'refused:invalid_request\n'
        file.write_bytes(too_big);file.chmod(0o600);result=subprocess.run([str(binary),str(ipc)],capture_output=True,timeout=10);assert result.returncode!=0
    print(json.dumps({'status':'passed','synthetic_only':True,'large_stdin_and_private_file_accepted':True,'oversize_refused':True}))
if __name__=='__main__':run()
