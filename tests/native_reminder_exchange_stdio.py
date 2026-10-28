"""Exercise request-bound Reminder evidence through real MCP stdio."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(policy):
    with tempfile.TemporaryDirectory(prefix='reminder-exchange-') as directory:
        home=Path(directory);runtime=home/'runtime'
        env={'HOME':directory,'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONUNBUFFERED':'1',
             'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
             'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
             'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'0','ICLOUD_ENABLE_REMINDERS':'1',
             'ICLOUD_NATIVE_CONFIG':json.dumps({'reminders':{'source_ids':['selected'],'access_mode':'read-only'}}),'ICLOUD_RUNTIME_ROOT':str(runtime)}
        with (home/'stderr').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_reminder_exchange_stdio_server.py')],cwd=directory,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence;process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush();deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic MCP exited')
                        value=json.loads(line)
                        if value.get('id')==current:return value
                raise AssertionError('Synthetic MCP timed out')
            def call(name,args):
                response=rpc('tools/call',{'name':name,'arguments':args})['result'];assert not response.get('isError'),response
                return response.get('structuredContent') or json.loads(response['content'][0]['text'])
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                plan=call('reminders_prepare_once',{'changes':[{'operation':'update_reminder','source_id':'selected','id':'task','fields':{'notes':'private synthetic after'}}]});identity=plan['plan_id']
                if policy=='local-receipt':
                    refused=rpc('tools/call',{'name':'reminders_execute_approved','arguments':{'plan_id':identity}})['result'];assert refused.get('isError')
                    fd=os.open(runtime/(identity+'.approved'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:json.dump({'plan_id':identity,'sha256':plan['sha256']},stream)
                result=call('reminders_execute_approved',{'plan_id':identity});assert result['status']=='failed_or_uncertain'
                observed=call('reminders_inspect_uncertain_change',{'plan_id':identity})
                assert observed['terminal_response']=='recorded' and observed['helper_reported_mutation_dispatched'] is True and observed['original_uncertainty_retained'] is True
                assert 'private synthetic' not in json.dumps(observed)
                for suffix in ('.native-reminder-request-0','.native-reminder-response-0'):
                    assert 'private synthetic' not in (runtime/(identity+suffix)).read_text()
                recovery=call('reminders_prepare_observation_recovery',{'plan_id':identity});rid=recovery['plan_id']
                assert 'delayed effects' in recovery['review']['payload']['effects']['uncertainty']
                if policy=='local-receipt':
                    refused=rpc('tools/call',{'name':'reminders_accept_observation_recovery','arguments':{'plan_id':rid}})['result'];assert refused.get('isError')
                    fd=os.open(runtime/(rid+'.approved'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:json.dump({'plan_id':rid,'sha256':recovery['sha256']},stream)
                accepted=call('reminders_accept_observation_recovery',{'plan_id':rid})
                assert accepted['native_data_changed'] is False and accepted['causal_dispatch_proven'] is False
                status=call('reminders_plan_status',{'plan_id':identity});assert status['original_status']=='failed_or_uncertain'
                assert rpc('tools/call',{'name':'reminders_execute_approved','arguments':{'plan_id':identity}})['result'].get('isError')
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr').read_text()
    return {'status':'passed','real_mcp_stdio':True,'synthetic_provider_only':True,'uncertainty_retained':True,'policy':policy}

if __name__=='__main__':print(json.dumps([run(policy) for policy in ('trusted-client','local-receipt')]))
