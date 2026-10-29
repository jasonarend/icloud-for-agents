"""Real MCP scoped mixed physical/Smart List group reads with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE='11111111-1111-4111-8111-111111111111';GROUP='22222222-2222-4222-8222-222222222222';SMART='44444444-4444-4444-8444-444444444444'

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-once-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':[SOURCE],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'} for n in ('reminders',)}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_group_members_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic server exited')
                        value=json.loads(line)
                        if value.get('id')==current:return value
                raise AssertionError('Synthetic MCP timeout')
            def call(name,args):
                response=rpc('tools/call',{'name':name,'arguments':args});assert 'error' not in response,response;return response['result']
            def data(response):
                assert not response.get('isError'),response
                return response.get('structuredContent') or json.loads(response['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-native-once','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                args={'source_id':SOURCE,'group_id':GROUP,'limit':1,'offset':0}
                ordinary=data(call('reminders_group_lists',args));assert ordinary['backend']=='reminderkit-group-lists-v2' and ordinary['done'] is True and 'kind' not in ordinary['lists'][0]
                first=data(call('reminders_group_lists',dict(args,include_smart_lists=True)));assert first['lists'][0]['kind']=='list' and first['custom_smart_lists_included'] is True and first['done'] is False
                second=data(call('reminders_group_lists',dict(args,include_smart_lists=True,offset=first['next_offset'])));assert second['lists'][0]['kind']=='smart_list' and second['done'] is True
                assert call('reminders_group_lists',dict(args,source_id=SMART)).get('isError')
                assert call('reminders_group_lists',dict(args,group_id=SMART,include_smart_lists=True)).get('isError')
                assert data(call('reminders_access_status',{}))['effective_access']=='read-only'
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['reminders'],'settings_remained_read_only':True,'mixed_native_group_pages_and_older_helper_refusal_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-GROUP-MEMBERS-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
