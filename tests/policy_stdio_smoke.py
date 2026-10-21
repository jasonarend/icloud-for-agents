"""Real JSON-RPC production-policy test, synthetic HOME/IMAP; no real account."""
import json,os,select,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(mode="trusted-client"):
    with tempfile.TemporaryDirectory(prefix='agents-policy-protocol-') as directory:
        home=Path(directory);audit=home/'audit.json'
        env={'HOME':str(home),'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT/'tests'),'PYTHONUNBUFFERED':'1','BRIDGE_TEST_AUDIT':str(audit),'FASTMCP_SHOW_SERVER_BANNER':'false','FASTMCP_CHECK_FOR_UPDATES':'off','ICLOUD_MAIL_ACCOUNT':'synthetic@example.com','ICLOUD_CLEANUP_GRANT_ID':'c'*32,'ICLOUD_MAIL_ACCESS':'write-enabled','ICLOUD_APPROVAL_POLICY':mode,'ICLOUD_ENABLE_POLICY_MANAGEMENT':'1'}
        with (home/'stderr.log').open('wb') as stderr:
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/managed_policy_stdio_server.py')],env=env,cwd=home,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr)
            sequence=0
            def rpc(method,params):
                nonlocal sequence
                sequence+=1;current=sequence
                process.stdin.write((json.dumps({'jsonrpc':'2.0','id':current,'method':method,'params':params})+'\n').encode());process.stdin.flush()
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if select.select([process.stdout],[],[],1)[0]:
                        line=process.stdout.readline()
                        if not line:raise AssertionError('Synthetic server exited')
                        result=json.loads(line)
                        if result.get('id')==current:return result
                raise AssertionError('Synthetic MCP timeout')
            def call(name,args):
                result=rpc('tools/call',{'name':name,'arguments':args})
                assert 'error' not in result,result
                return result['result']
            def data(result):
                assert not result.get('isError'),result
                return result.get('structuredContent') or json.loads(result['content'][0]['text'])
            def exception(uid):
                mail=data(call('email_prepare_trash',{'folder':'INBOX','uidvalidity':71,'uids':[uid],'reason':'Synthetic explicit owner request'}))
                return data(call('policy_prepare_exception',{'mail_plan_id':mail['plan_id'],'deviations':['starred']}))
            def owner_review(p):
                if mode != 'local-receipt':
                    return
                # Separate synthetic owner component invokes the actual TTY interface.
                # This is a simulated TTY test, never a claimed real human approval.
                code = """from io import StringIO
from icloud_for_agents.policy_management import PolicyManager
from icloud_for_agents.approvals import runtime_root
from icloud_for_agents.policy_approval import approve_local
class SyntheticOwnerTTY(StringIO):
 def readline(self): return {phrase!r}+'\\n'
 def __exit__(self,*args): pass
approve_local(PolicyManager(runtime_root()/'managed-policy'),{proposal!r},lambda:SyntheticOwnerTTY())
""".format(phrase='APPROVE POLICY '+p['proposal']['plan_id']+' '+p['sha256'],proposal=p['proposal']['plan_id'])
                reviewed=subprocess.run([sys.executable,'-c',code],env=env,cwd=home,capture_output=True)
                assert reviewed.returncode==0,reviewed.stderr.decode()

            def execute(p,sha=None):
                return call('policy_execute_approved',{'proposal_id':p['proposal']['plan_id'],'expected_hash':sha or p['sha256'],'actor_reference':'synthetic explicit chat confirmation; client assertion'})
            try:
                rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'synthetic-policy-client','version':'1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');process.stdin.flush()
                names={t['name'] for t in rpc('tools/list',{})['result']['tools']}
                assert 'policy_prepare_exception' in names and len(names)==15 # ten Mail + five policy tools
                status=data(call('policy_status',{}));policy=status['current']['policy']
                p=exception(1)
                if mode == 'local-receipt':
                    assert execute(p).get('isError')
                owner_review(p)
                assert execute(p,'0'*64).get('isError')
                outcome=data(execute(p));assert outcome['status']=='moved'
                assert data(call('policy_status',{'proposal_id':p['proposal']['plan_id']}))['outcome']['status']=='completed'
                assert execute(p).get('isError')
                revoked=exception(2);assert data(call('policy_revoke_proposal',{'proposal_id':revoked['proposal']['plan_id']}))['status']=='revoked'
                assert execute(revoked).get('isError')
                assert data(call('policy_status',{'proposal_id':revoked['proposal']['plan_id']}))['outcome']['status']=='revoked'
                assert call('policy_prepare_change',{'replacement':dict(policy,account='other@example.com')}).get('isError')
                assert call('policy_prepare_change',{'replacement':dict(policy,operations=['purge'])}).get('isError')
                change=data(call('policy_prepare_change',{'replacement':dict(policy,max_batch=30)}))
                owner_review(change)
                assert data(execute(change))['revision']==2
                assert data(call('policy_status',{}))['current']['policy']['max_batch']==30
            finally:
                process.stdin.close()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode==0
        assert json.loads(audit.read_text())['blocked']==[]
        return {'status':'passed','transport':'real stdio JSON-RPC','synthetic_only':True,'proposal_approval_execution':'trusted-client assertion' if mode=='trusted-client' else 'strict local receipt with separately simulated owner TTY','exact_exception_moved':True,'replay_altered_hash_revoked_account_scope_refused':True,'persistent_change_verified':True,'network_credential_attempts':[]}

if __name__=='__main__':
    result={'trusted_client':run('trusted-client'),'strict_receipt':run('local-receipt')};(ROOT/'build/reports').mkdir(parents=True,exist_ok=True)
    (ROOT/'build/reports/POLICY-STDIO.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
