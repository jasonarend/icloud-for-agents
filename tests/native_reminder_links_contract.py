"""Run shipped passive-link validators without any store or permission."""
import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run():
    source=(ROOT/'src/icloud_for_agents/native/reminders.swift').read_text()
    start=source.index('func reminderLink(');functions=source[start:source.index('func ordinaryTimeAlarm(',start)]
    with tempfile.TemporaryDirectory(prefix='native-passive-metadata-') as directory:
        path=Path(directory);script=path/'metadata.swift';binary=path/'metadata'
        script.write_text('import Foundation\nfunc fail(_ reason:String)->Never {exit(3)}\n'+functions+'''
let input=try JSONSerialization.jsonObject(with:FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
let result:[String:Any]=["url":reminderLink(input["url"]!)?.absoluteString ?? NSNull() as Any]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:result))
''')
        subprocess.run(['/usr/bin/swiftc',str(script),'-o',str(binary)],capture_output=True,check=True,timeout=60)
        def call(url,location='Synthetic room'):
            return subprocess.run([str(binary)],input=json.dumps({'url':url,'location':location}),capture_output=True,text=True,timeout=5)
        for url in ('https://example.com/a%20b?x=1#section','applenotes:note/SYNTHETIC','x-apple-reminderkit://SYNTHETIC',None):
            result=call(url);assert result.returncode==0,(url,result.stderr);assert json.loads(result.stdout)=={'url':url}
        result=call(None,None);assert json.loads(result.stdout)=={'url':None}
        for url in ('file:///tmp/example','https://user:pass@example.com','javascript:alert(1)','applenotes:',
                    'https://example.com/space here','https://example.com/%broken','HTTPS://example.com/',
                    'https://example.com:0/','https://example.com:65536/','https://example.com/<html>'):
            assert call(url).returncode==3,url
    return {'status':'passed','live_store_or_permission_used':False,'checks':['exact_web_and_native_links','null_clear','credentials_and_active_schemes_refused','silent_URL_repairs_refused']}

if __name__=='__main__':
    result=run();(ROOT/'build/reports/NATIVE-REMINDER-LINKS-CONTRACT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
