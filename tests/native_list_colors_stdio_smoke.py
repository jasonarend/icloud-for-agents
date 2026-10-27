"""Real MCP list colors and stable ongoing scope with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-once-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':['selected']} for n in ('notes','reminders')}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_list_colors_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                def approve(plan):
                    if policy=='local-receipt':
                        # Isolated synthetic independent owner fixture only.
                        receipt=runtime/(plan['plan_id']+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:json.dump({'plan_id':plan['plan_id'],'sha256':plan['sha256']},stream)
                def change(color):return {'operation':'set_list_color','source_id':'selected','list_id':'list','fields':{'color':color}}
                assert call('reminders_prepare_changes',{'changes':[change('#123456')]}).get('isError')
                plan=data(call('reminders_prepare_once',{'changes':[change('#123456')]}));identity=plan['plan_id']
                if policy=='local-receipt':assert call('reminders_execute_approved',{'plan_id':identity}).get('isError')
                approve(plan);assert data(call('reminders_execute_approved',{'plan_id':identity}))['status']=='applied'
                assert call('reminders_execute_approved',{'plan_id':identity}).get('isError')
                scope={'scenario':'Synthetic exact list colors','rules':[{'operation':'set_list_color','source_id':'selected','container_id':'list','fields':['color'],'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
                grant=data(call('reminders_prepare_grant',{'scope':scope}));approve(grant)
                assert data(call('reminders_activate_grant',{'grant_id':grant['plan_id']}))['grant_active'] is True
                for title in ('#ABCDEF','#336699'):
                    batch=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[change(title)]}))
                    assert data(call('reminders_execute_approved',{'plan_id':batch['plan_id']}))['status']=='applied'
                    assert data(call('reminders_grant_status',{'grant_id':grant['plan_id']}))['status']=='active'
                    assert data(call('reminders_access_status',{}))['effective_access']=='read-only'
                pending=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[change('#ffffff')]}))
                assert data(call('reminders_revoke_grant',{'grant_id':grant['plan_id']}))['status']=='revoked'
                assert call('reminders_execute_approved',{'plan_id':pending['plan_id']}).get('isError')
                for color,target,expected in (('#000011','before','restored_before_after_explicit_correction'),('#000022','requested','verified_after_explicit_correction')):
                    failed=data(call('reminders_prepare_once',{'changes':[change(color)]}));approve(failed)
                    assert data(call('reminders_execute_approved',{'plan_id':failed['plan_id']}))['status']=='failed_or_uncertain'
                    repair=data(call('reminders_prepare_update_correction',{'plan_id':failed['plan_id'],'target':target}))
                    if policy=='local-receipt':assert call('reminders_execute_approved',{'plan_id':repair['plan_id']}).get('isError')
                    approve(repair);assert data(call('reminders_execute_approved',{'plan_id':repair['plan_id']}))['status']=='applied'
                    outcome=data(call('reminders_reconcile_correction',{'plan_id':repair['plan_id']}));assert outcome['status']==expected,outcome
                    if target=='before':assert outcome['original_request_succeeded'] is False
                    assert call('reminders_execute_approved',{'plan_id':failed['plan_id']}).get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['reminders'],'settings_remained_read_only':True,'list_colors_once_standing_color_stability_and_revocation_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-LIST-COLORS-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
