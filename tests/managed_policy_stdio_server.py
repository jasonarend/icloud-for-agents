"""Synthetic production-policy target for the isolated protocol test only."""
import atexit,json,os,sys,time
from pathlib import Path
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise RuntimeError('Synthetic policy server forbids networking and child processes')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps({'blocked':blocked})))
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.cleanup_access import CleanupAccess
from icloud_for_agents.managed_policy import initialize_from_base
PlanStore().ready();account=os.environ['ICLOUD_MAIL_ACCOUNT'];grant=os.environ['ICLOUD_CLEANUP_GRANT_ID']
access=CleanupAccess(grant,account)
access.authority.write_once(access.authority.path(grant,'.json'),{'grant_id':grant,'account':account,'operations':['archive','mark_read','trash'],'source_folders':['INBOX'],'destinations':{'archive':'Retained','trash':'Bin'},'max_batch':25,'max_24h':None,'issued':time.time()-1,'expires':None,'rules_reference':'Synthetic owner rules'})
initialize_from_base(account,grant)
from icloud_for_agents import credentials
def no_credentials():blocked.append('credential_lookup');raise AssertionError('No real credentials')
credentials.credentials=no_credentials
from icloud_for_agents import server
from icloud_for_agents.service import MailService
from test_mail_safety import FakeIMAP
client=FakeIMAP((1,2,3));server._service=MailService(account,'synthetic',connector=lambda:client)
server.main()
