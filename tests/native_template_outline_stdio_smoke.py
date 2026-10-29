"""Actual MCP fixed-query template outlines over isolated synthetic stores."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from test_native_template_outline import SOURCE
FILE_SOURCE=SOURCE

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-once-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':[SOURCE],'metadata_backend':'reminders-store-readonly','advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_template_outline_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                from test_native_template_outline import SOURCE as OUTLINE_SOURCE,TEMPLATE,ROOT as ITEM_ROOT,CHILD,OTHER
                args={'source_id':OUTLINE_SOURCE,'id':TEMPLATE,'limit':1}
                first=data(call('reminders_template_outline',args));assert first['total_items']==2 and first['items'][0]['item_id']==ITEM_ROOT and first['done'] is False and first['next_offset']==1
                child=data(call('reminders_template_outline',dict(args,offset=1)));assert child['done'] is True and child['items'][0]['item_id']==CHILD and child['items'][0]['parent_item_id']==ITEM_ROOT
                assert first['content_complete'] is False and first['database_writes'] is False and first['instructions_trusted'] is False and first['item_kind']=='template_saved_item_not_live_reminder'
                assert 'notes' in first['omitted_features'] and 'Private body blob' not in json.dumps(first)
                for bad in (dict(args,source_id=OTHER),dict(args,id='foreign'),dict(args,limit=21),dict(args,offset=-1),dict(args,output_path='/tmp/not-allowed')):
                    assert call('reminders_template_outline',bad).get('isError')
                assert data(call('reminders_access_status',{}))['effective_access']=='read-only'

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['reminders'],'settings_remained_read_only':True,'template_outline_scope_pagination_and_omissions_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-TEMPLATE-OUTLINE-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
