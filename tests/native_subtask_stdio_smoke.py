"""Real MCP native recurrence configuration and scenario scope with isolated synthetic providers."""
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
            'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'access_mode':'read-only','source_ids':['selected'],'advanced_backend':'reminderkit','helper_path':'/synthetic/helper'}}),
            'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_subtask_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                def change(title,parent='created'):
                    return {'operation':'create_subtask','source_id':'selected','id':parent,'fields':{'title':title}}
                assert call('reminders_prepare_changes',{'changes':[change('Synthetic one')]}).get('isError')
                plan=data(call('reminders_prepare_once',{'changes':[change('Synthetic one')]}));identity=plan['plan_id']
                if policy=='local-receipt':assert call('reminders_execute_approved',{'plan_id':identity}).get('isError')
                approve(plan);assert data(call('reminders_execute_approved',{'plan_id':identity}))['status']=='applied'
                assert call('reminders_execute_approved',{'plan_id':identity}).get('isError')
                scope={'scenario':'Synthetic parent-scoped subtasks','rules':[{'operation':'create_subtask','source_id':'selected','container_id':'list','fields':['title'],'destinations':['created']}],'max_batch':1,'max_24h':2,'expires':None}
                invalid=json.loads(json.dumps(scope));invalid['rules'][0]['destinations']=['other']
                assert call('reminders_prepare_grant',{'scope':invalid}).get('isError')
                grant=data(call('reminders_prepare_grant',{'scope':scope}));approve(grant)
                assert data(call('reminders_activate_grant',{'grant_id':grant['plan_id']}))['grant_active'] is True
                batch=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[change('Synthetic two')]}))
                assert data(call('reminders_execute_approved',{'plan_id':batch['plan_id']}))['status']=='applied'
                assert data(call('reminders_access_status',{}))['effective_access']=='read-only'
                assert data(call('reminders_revoke_grant',{'grant_id':grant['plan_id']}))['status']=='revoked'
                assert call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[change('Synthetic three')]}).get('isError')
                def section(title):return {'operation':'create_section','source_id':'selected','list_id':'list','fields':{'title':title}}
                plan=data(call('reminders_prepare_once',{'changes':[section('Synthetic one')]}))
                if policy=='local-receipt':assert call('reminders_execute_approved',{'plan_id':plan['plan_id']}).get('isError')
                approve(plan);assert data(call('reminders_execute_approved',{'plan_id':plan['plan_id']}))['status']=='applied'
                assert call('reminders_execute_approved',{'plan_id':plan['plan_id']}).get('isError')
                scope={'scenario':'Synthetic list sections','rules':[{'operation':'create_section','source_id':'selected','container_id':'list','fields':['title'],'destinations':[]}],'max_batch':1,'max_24h':1,'expires':None}
                grant=data(call('reminders_prepare_grant',{'scope':scope}));approve(grant);assert data(call('reminders_activate_grant',{'grant_id':grant['plan_id']}))['grant_active'] is True
                batch=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[section('Synthetic two')]}));assert data(call('reminders_execute_approved',{'plan_id':batch['plan_id']}))['status']=='applied';second_id=batch['review']['payload']['changes'][0]['section_id']
                quota=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[section('Synthetic quota')]}))
                assert call('reminders_execute_approved',{'plan_id':quota['plan_id']}).get('isError')
                assert data(call('reminders_revoke_grant',{'grant_id':grant['plan_id']}))['status']=='revoked'
                assert call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[section('Synthetic three')]}).get('isError')
                section_id=plan['review']['payload']['changes'][0]['section_id']
                def rename(sid,title):return {'operation':'rename_section','source_id':'selected','list_id':'list','section_id':sid,'fields':{'title':title}}
                rename_plan=data(call('reminders_prepare_once',{'changes':[rename(section_id,'Renamed once')]}));approve(rename_plan);assert data(call('reminders_execute_approved',{'plan_id':rename_plan['plan_id']}))['status']=='applied'
                scope={'scenario':'Synthetic section rename','rules':[{'operation':'rename_section','source_id':'selected','container_id':'list','fields':['title'],'destinations':[section_id]}],'max_batch':1,'max_24h':2,'expires':None}
                grant=data(call('reminders_prepare_grant',{'scope':scope}));approve(grant);assert data(call('reminders_activate_grant',{'grant_id':grant['plan_id']}))['grant_active'] is True
                batch=data(call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[rename(section_id,'Renamed by scenario')]}));assert data(call('reminders_execute_approved',{'plan_id':batch['plan_id']}))['status']=='applied'
                assert call('reminders_prepare_with_grant',{'grant_id':grant['plan_id'],'changes':[rename(second_id,'Not allowed')]}).get('isError')
                assert data(call('reminders_revoke_grant',{'grant_id':grant['plan_id']}))['status']=='revoked'

            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['reminders'],'settings_remained_read_only':True,'subtask_once_parent_scope_and_revocation_verified':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-SUBTASK-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
