"""Real MCP one-time/scenario subtree permissions with isolated providers."""
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
            'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['11111111-1111-4111-8111-111111111111'],'advanced_backend':'reminderkit'}}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_reminder_move_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
            def authorize(proposal,tool,arg):
                if policy=='local-receipt':
                    assert call(tool,{arg:proposal['plan_id']}).get('isError')
                    # Test-only synthetic owner receipt, never a real approval.
                    receipt=runtime/(proposal['plan_id']+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:json.dump({'plan_id':proposal['plan_id'],'sha256':proposal['sha256']},stream)
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-subtree-move','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                SOURCE='11111111-1111-4111-8111-111111111111';ORIGIN='22222222-2222-4222-8222-222222222222';DEST='33333333-3333-4333-8333-333333333333';TASK='44444444-4444-4444-8444-444444444444';CHILD='55555555-5555-4555-8555-555555555555'
                def changes(destination):return [{'operation':'move_reminder','source_id':SOURCE,'id':TASK,'fields':{'list_id':destination}}]
                assert call('reminders_prepare_changes',{'changes':changes(DEST)}).get('isError')
                for destination in (DEST,ORIGIN):
                    proposal=data(call('reminders_prepare_once',{'changes':changes(destination)}));assert proposal['review']['payload']['changes'][0]['effects']['quota_objects']==2
                    authorize(proposal,'reminders_execute_approved','plan_id')
                    outcome=data(call('reminders_execute_approved',{'plan_id':proposal['plan_id']}));assert outcome['status']=='applied' and outcome['completed'][0]['result']['moved_ids']==sorted([TASK,CHILD])
                    assert call('reminders_execute_approved',{'plan_id':proposal['plan_id']}).get('isError')
                scope={'scenario':'Move synthetic parent with child','rules':[{'operation':'move_reminder','source_id':SOURCE,'container_id':left,'fields':['list_id'],'destinations':[right]} for left,right in ((ORIGIN,DEST),(DEST,ORIGIN))],'max_batch':1,'max_24h':4,'expires':None}
                small=data(call('reminders_prepare_grant',{'scope':scope}));authorize(small,'reminders_activate_grant','grant_id');data(call('reminders_activate_grant',{'grant_id':small['plan_id']}))
                assert call('reminders_prepare_with_grant',{'grant_id':small['plan_id'],'changes':changes(DEST)}).get('isError');data(call('reminders_revoke_grant',{'grant_id':small['plan_id']}))
                scope['max_batch']=2;grant=data(call('reminders_prepare_grant',{'scope':scope}));authorize(grant,'reminders_activate_grant','grant_id');data(call('reminders_activate_grant',{'grant_id':grant['plan_id']}))
                for destination in (DEST,ORIGIN):
                    batch=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':changes(destination)}));assert batch['authorization_path']=='standing-native'
                    assert data(call('reminders_execute_approved',{'plan_id':batch['plan_id']}))['status']=='applied'
                    assert json.loads((runtime/(batch['plan_id']+'.native-grant-use')).read_text())['count']==2
                pending=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':changes(DEST)}));assert call('reminders_execute_approved',{'plan_id':pending['plan_id']}).get('isError');data(call('reminders_cancel_plan',{'plan_id':pending['plan_id']}))
                assert data(call('reminders_revoke_grant',{'grant_id':grant['plan_id']}))['status']=='revoked';assert call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':changes(DEST)}).get('isError')
                assert data(call('reminders_access_status',{}))['effective_access']=='read-only'
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['reminders'],'settings_remained_read_only':True,'subtree_preservation_and_descendant_quotas_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-REMINDER-MOVE-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
