"""Exercise complete selected-list hierarchy through real MCP using synthetic data."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE='11111111-1111-4111-8111-111111111111';LIST='22222222-2222-4222-8222-222222222222'
def run():
    with tempfile.TemporaryDirectory(prefix='native-list-graph-') as d:
        env={'HOME':d,'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(Path(d)/'audit.json'),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1','ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':[SOURCE],'access_mode':'read-only','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}),'ICLOUD_RUNTIME_ROOT':str(Path(d)/'runtime')}
        with (Path(d)/'stderr').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_list_hierarchy_stdio_server.py')],cwd=d,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic MCP exited')
                        result=json.loads(line)
                        if result.get('id')==current:return result
                raise AssertionError('Synthetic MCP timed out')
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-group','version':'1'}});process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                result=rpc('tools/call',{'name':'reminders_list_hierarchy','arguments':{'list_id':LIST}})['result'];assert not result.get('isError'),result
                value=result.get('structuredContent') or json.loads(result['content'][0]['text']);assert len(value['nodes'])==2 and value['nodes'][1]['parent_id']==value['nodes'][0]['id'] and value['hierarchy_complete'] is True and value['data_mutated'] is False and value['membership_verified'] is True
                refused=rpc('tools/call',{'name':'reminders_list_hierarchy','arguments':{'list_id':'unselected'}})['result'];assert refused.get('isError')
                refused=rpc('tools/call',{'name':'reminders_list_hierarchy','arguments':{'list_id':LIST,'source_id':'foreign'}})['result'];assert refused.get('isError')

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
            assert process.returncode==0;assert json.loads((Path(d)/'audit.json').read_text())==[]
    print(json.dumps({'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True}))
if __name__=='__main__':run()
