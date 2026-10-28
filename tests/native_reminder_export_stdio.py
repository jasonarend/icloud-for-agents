"""Exercise read-only JSON export over actual MCP stdio with synthetic data."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run():
    with tempfile.TemporaryDirectory(prefix='reminder-export-') as directory:
        env={'HOME':directory,'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1',
             'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
             'ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0',
             'ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected'],'access_mode':'read-only'}})}
        with open(Path(directory)/'stderr','wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_reminder_export_stdio_server.py')],cwd=directory,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr)
            sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence
                process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush()
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('MCP server exited')
                        value=json.loads(line)
                        if value.get('id')==current:return value
                raise AssertionError('MCP request timed out')
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                result=rpc('tools/call',{'name':'reminders_export_page','arguments':{'list_id':'list'}})['result']
                assert not result.get('isError'),result
                value=result.get('structuredContent') or json.loads(result['content'][0]['text'])
                assert value['items'][0]['title']=='Synthetic title' and value['coverage']['restorable'] is False
                assert value['coverage']['complete_backup'] is False and value['page']['done'] is True
                ical=rpc('tools/call',{'name':'reminders_export_ical','arguments':{'id':'task'}})['result'];assert not ical.get('isError'),ical
                calendar=ical.get('structuredContent') or json.loads(ical['content'][0]['text']);assert 'BEGIN:VTODO\r\n' in calendar['content'] and calendar['coverage']['restorable'] is False
                assert rpc('tools/call',{'name':'reminders_export_page','arguments':{'list_id':'list','limit':21}})['result'].get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(Path(directory)/'stderr').read_text()
    print(json.dumps({'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True,'read_only':True}))
if __name__=='__main__':run()
