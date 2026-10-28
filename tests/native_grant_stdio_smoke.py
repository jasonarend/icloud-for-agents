"""Real MCP ongoing scenario permissions with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(policy):
    with tempfile.TemporaryDirectory(prefix='native-grant-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'1',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':['selected']} for n in ('notes','reminders')}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_grant_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-native-grants','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                for name in ('notes','reminders'):
                    operation='create_note' if name=='notes' else 'create_reminder'
                    fields={'title':'Synthetic','body':'Test'} if name=='notes' else {'title':'Synthetic','notes':'Test'}
                    container='folder_id' if name=='notes' else 'list_id'
                    changes=[{'operation':operation,'source_id':'selected',container:'folder','fields':fields}]
                    scope={'scenario':'Synthetic MCP scenario','rules':[{'operation':operation,'source_id':'selected','container_id':'folder','fields':list(fields),'destinations':[]}],'max_batch':2,'max_24h':4,'expires':None}
                    proposal=data(call(name+'_prepare_grant',{'scope':scope}));identity=proposal['plan_id']
                    assert data(call(name+'_grant_status',{'grant_id':identity}))['status']=='pending_activation'
                    if policy=='local-receipt':
                        assert call(name+'_activate_grant',{'grant_id':identity}).get('isError')
                        # Separate synthetic owner receipt fixture; not real human approval.
                        receipt=runtime/(identity+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:json.dump({'plan_id':identity,'sha256':proposal['sha256']},stream)
                    assert data(call(name+'_activate_grant',{'grant_id':identity}))['grant_active']
                    assert call(name+'_activate_grant',{'grant_id':identity}).get('isError')
                    for _ in range(2):
                        plan=data(call(name+'_prepare_with_grant',{'grant_id':identity,'changes':changes}))
                        assert plan['authorization_path']=='standing-native' and not plan['local_receipt_required']
                        assert data(call(name+'_execute_approved',{'plan_id':plan['plan_id']}))['status']=='applied'
                        assert not (runtime/(plan['plan_id']+'.approved')).exists()
                        assert call(name+'_execute_approved',{'plan_id':plan['plan_id']}).get('isError')
                    assert data(call(name+'_access_status',{}))['effective_access']=='read-only'
                    invalid=[dict(changes[0],fields=dict(fields,unreviewed='No'))]
                    assert call(name+'_prepare_with_grant',{'grant_id':identity,'changes':invalid}).get('isError')
                    pending=data(call(name+'_prepare_with_grant',{'grant_id':identity,'changes':changes}))
                    replacement=data(call(name+'_prepare_grant_replacement',{'grant_id':identity,'scope':dict(scope,scenario='Revised synthetic scenario',max_24h=2)}))
                    new_id=replacement['plan_id']
                    if policy=='local-receipt':
                        assert call(name+'_activate_grant',{'grant_id':new_id}).get('isError')
                        receipt=runtime/(new_id+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:json.dump({'plan_id':new_id,'sha256':replacement['sha256']},stream)
                    assert data(call(name+'_activate_grant',{'grant_id':new_id}))['grant_active']
                    assert data(call(name+'_grant_status',{'grant_id':identity}))['status']=='revoked'
                    assert call(name+'_execute_approved',{'plan_id':pending['plan_id']}).get('isError')
                    capped=data(call(name+'_prepare_with_grant',{'grant_id':new_id,'changes':changes}))
                    assert call(name+'_execute_approved',{'plan_id':capped['plan_id']}).get('isError')
                    assert data(call(name+'_cancel_plan',{'plan_id':capped['plan_id']}))['status']=='cancelled'
                    assert data(call(name+'_revoke_grant',{'grant_id':new_id}))['status']=='revoked'
                    assert data(call(name+'_revoke_grant',{'grant_id':identity}))['status']=='revoked'
                    assert data(call(name+'_grant_status',{'grant_id':identity}))['status']=='revoked'
                    assert call(name+'_execute_approved',{'plan_id':pending['plan_id']}).get('isError')
                    assert call(name+'_prepare_with_grant',{'grant_id':identity,'changes':changes}).get('isError')
                    copy_fields=['title','body','format'] if name=='notes' else ['title','notes','priority']
                    copy_scope={'scenario':'Synthetic observation copies','rules':[{'operation':'copy_observation','source_id':'selected','container_id':'folder','fields':copy_fields,'destinations':[]}],'max_batch':1,'max_24h':2,'expires':None}
                    copy_grant=data(call(name+'_prepare_grant',{'scope':copy_scope}));assert 'lossy copies' in copy_grant['review']['payload']['effects']['observation_copy']
                    if policy=='local-receipt':
                        assert call(name+'_activate_grant',{'grant_id':copy_grant['plan_id']}).get('isError')
                        receipt=runtime/(copy_grant['plan_id']+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                        with os.fdopen(fd,'w') as stream:json.dump({'plan_id':copy_grant['plan_id'],'sha256':copy_grant['sha256']},stream)
                    assert data(call(name+'_activate_grant',{'grant_id':copy_grant['plan_id']}))['grant_active']
                    assert call(name+'_prepare_with_grant',{'grant_id':copy_grant['plan_id'],'changes':changes}).get('isError')
                    if name=='notes':
                        snapshot={'format':'icloud-for-agents-note-observation','version':1,'item':{'id':'old','title':'Synthetic','html':'<div>Synthetic</div><div>Body</div>','locked':False,'truncated':False,'content_complete':False}}
                        args={'snapshot':snapshot,'source_id':'selected','folder_id':'folder','accept_omissions':True,'grant_id':copy_grant['plan_id']}
                    else:
                        snapshot={'format':'icloud-for-agents-reminders-observation','version':1,'list_id':'old-list','page':{'done':True},'items':[{'id':'old','list_id':'old-list','title':'Synthetic','notes':'Text','priority':1,'completed':False}]}
                        args={'snapshot':snapshot,'source_id':'selected','list_id':'folder','accept_omissions':True,'grant_id':copy_grant['plan_id']}
                    copied=data(call(name+'_prepare_observation_copy',args));assert copied['authorization_path']=='standing-native' and copied['review']['payload']['copy_review']['accepted_omissions'] is True
                    assert data(call(name+'_execute_approved',{'plan_id':copied['plan_id']}))['status']=='applied'
                    assert call(name+'_execute_approved',{'plan_id':copied['plan_id']}).get('isError')
                    if name=='reminders':
                        ical='BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Synthetic//EN\r\nBEGIN:VTODO\r\nUID:synthetic\r\nDTSTAMP:20260101T000000Z\r\nSUMMARY:Imported task\r\nSTATUS:NEEDS-ACTION\r\nPRIORITY:0\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'
                        imported=data(call('reminders_prepare_ical_copy',{'calendar_text':ical,'source_id':'selected','list_id':'folder','accept_omissions':True,'grant_id':copy_grant['plan_id']}))
                        assert imported['authorization_path']=='standing-native' and imported['review']['payload']['copy_review']['source_format']=='user_supplied_icalendar'
                        assert data(call('reminders_execute_approved',{'plan_id':imported['plan_id']}))['status']=='applied'
                    assert data(call(name+'_revoke_grant',{'grant_id':copy_grant['plan_id']}))['status']=='revoked'
                    assert call(name+'_prepare_observation_copy',args).get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['notes','reminders'],'settings_remained_read_only':True,'scenario_activation_scope_replay_revocation_verified':True,'replacement_retires_prior_scope_and_carries_quota':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-GRANT-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
