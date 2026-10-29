"""Real MCP known-move observation recovery with isolated synthetic providers."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def run(policy,folder_mode=False):
    with tempfile.TemporaryDirectory(prefix='native-move-observation-') as directory:
        home=Path(directory);audit=home/'audit.json';runtime=home/'runtime'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),
            'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off',
            'ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_MAIL_ACCESS':'read-only','ICLOUD_APPROVAL_POLICY':policy,
            'ICLOUD_ENABLE_MAIL':'0','ICLOUD_ENABLE_CALENDAR':'0','ICLOUD_ENABLE_CONTACTS':'0','ICLOUD_ENABLE_NOTES':'1','ICLOUD_ENABLE_REMINDERS':'0',
            'ICLOUD_NATIVE_CONFIG':json.dumps({n:{'access_mode':'read-only','source_ids':['selected']} for n in ('notes','reminders')}),
            'ICLOUD_RUNTIME_ROOT':str(runtime),'BRIDGE_TEST_ORIGINAL':str(home/'original.json')}
        if folder_mode:
            from test_native_folder_observation import S
            env.update(BRIDGE_TEST_FOLDER_MODE='1',ICLOUD_NATIVE_CONFIG=json.dumps({'notes':{'access_mode':'read-only','source_ids':[S],'metadata_backend':'notes-store-readonly'}}))
        expected_location='marked_deleted' if folder_mode else 'source'
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/native_move_recovery_stdio_server.py')],cwd=home,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr);sequence=0
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
                original=json.loads((home/'original.json').read_text())
                p=data(call('notes_prepare_move_recovery',{'plan_id':original['plan_id']}))
                assert data(call('notes_abandon_move_recovery',{'plan_id':p['plan_id']}))['native_data_changed'] is False
                assert call('notes_accept_move_recovery',{'plan_id':p['plan_id']}).get('isError')
                plan=data(call('notes_prepare_move_recovery',{'plan_id':original['plan_id']}));identity=plan['plan_id']
                assert plan['review']['payload']['observed']['location']==expected_location
                if policy=='local-receipt':
                    assert call('notes_accept_move_recovery',{'plan_id':identity}).get('isError')
                    # Separate synthetic owner fixture, never real human approval.
                    receipt=runtime/(identity+'.approved');fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:json.dump({'plan_id':identity,'sha256':plan['sha256']},stream)
                result=data(call('notes_accept_move_recovery',{'plan_id':identity}))
                assert result['recovery_state']=='observed_known_move_state_accepted_after_review'
                assert result['native_data_changed'] is False and result['causal_dispatch_proven'] is False
                status=data(call('notes_plan_status',{'plan_id':original['plan_id']}))
                assert status['original_status']=='failed_or_uncertain' and status['location']==expected_location
                assert data(call('notes_finalize_move_recovery',{'plan_id':identity}))['status']=='observed_known_move_state_accepted'
                assert call('notes_accept_move_recovery',{'plan_id':identity}).get('isError')
                assert call('notes_execute_approved',{'plan_id':original['plan_id']}).get('isError')
                assert data(call('notes_access_status',{}))['effective_access']=='read-only'
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0,(home/'stderr.log').read_text();assert json.loads(audit.read_text())==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_providers_only':True,'policy':policy,'services':['notes'],'observation':'experimental_empty_folder' if folder_mode else 'note','settings_remained_read_only':True,'observation_original_failure_and_no_causal_claim_verified':True,'old_plan_replay_refused':True,'network_helper_attempts':[]}

if __name__=='__main__':
    results=[run(policy,folder) for folder in (False,True) for policy in ('trusted-client','local-receipt')];path=ROOT/'build/reports/NATIVE-MOVE-RECOVERY-STDIO.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
