"""Real MCP temporary elevation/cancellation with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-once-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':['selected']} for n in ('notes','reminders')}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_once_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                for name in ('notes','reminders'):
                    changes=[{'operation':'update','source_id':'selected','id':'one','fields':{'title':'After'}}]
                    assert call(name+'_prepare_changes',{'changes':changes}).get('isError')
                    plan=data(call(name+'_prepare_once',{'changes':changes}));plan_id=plan['plan_id']
                    assert data(call(name+'_access_status',{}))['effective_access']=='read-only'
                    if policy=='local-receipt':
                        assert call(name+'_execute_approved',{'plan_id':plan_id}).get('isError')
                        # Test-only independent synthetic owner fixture, never real human consent.
                        receipt=runtime/(plan_id+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:json.dump({'plan_id':plan_id,'sha256':plan['sha256']},stream)
                    assert data(call(name+'_execute_approved',{'plan_id':plan_id}))['status']=='applied'
                    assert call(name+'_execute_approved',{'plan_id':plan_id}).get('isError')
                    assert data(call(name+'_access_status',{}))['effective_access']=='read-only'
                    cancelled=data(call(name+'_prepare_once',{'changes':changes}))
                    assert data(call(name+'_cancel_plan',{'plan_id':cancelled['plan_id']}))['status']=='cancelled'
                    assert call(name+'_execute_approved',{'plan_id':cancelled['plan_id']}).get('isError')
                    assert data(call(name+'_plan_status',{'plan_id':cancelled['plan_id']}))['status']=='cancelled'
                snapshot={'format':'icloud-for-agents-reminders-observation','version':1,'list_id':'original-list','page':{'done':False},'items':[{'id':'old','list_id':'original-list','title':'Synthetic copy','notes':'Text','priority':1,'completed':True}]}
                assert call('reminders_prepare_observation_copy',{'snapshot':snapshot,'source_id':'selected','list_id':'destination','accept_omissions':False}).get('isError')
                copied=data(call('reminders_prepare_observation_copy',{'snapshot':snapshot,'source_id':'selected','list_id':'destination','accept_omissions':True}))
                assert copied['review']['payload']['copy_review']['source_completed_count']==1 and copied['review']['payload']['copy_review']['page_done'] is False
                if policy=='local-receipt':
                    assert call('reminders_execute_approved',{'plan_id':copied['plan_id']}).get('isError')
                    receipt=runtime/(copied['plan_id']+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:json.dump({'plan_id':copied['plan_id'],'sha256':copied['sha256']},stream)
                assert data(call('reminders_execute_approved',{'plan_id':copied['plan_id']}))['status']=='applied'
                assert call('reminders_execute_approved',{'plan_id':copied['plan_id']}).get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['notes','reminders'],'settings_remained_read_only':True,'replay_and_cancelled_execution_refused':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-ONCE-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
