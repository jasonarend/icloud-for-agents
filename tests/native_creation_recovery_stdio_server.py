"""Production move-observation MCP handlers with a historical synthetic failure."""
import atexit,json,os,sys
from pathlib import Path
from unittest.mock import patch
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic observation server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_recovery as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import digest
from icloud_for_agents.native_once import planning
from test_native_recovery import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
account='synthetic@example.com';store=PlanStore();adapter=Adapter();adapter.full=os.environ['BRIDGE_TEST_ACTION']=='accept_observed_creation';recovery=module.NotesCreationRecovery(account,store,adapter)
with planning('notes'):
    item=adapter.prepare({'operation':'create_note','source_id':'selected','folder_id':'folder','fields':{'title':'Spec','body':'Original requested body'}})
# Historical synthetic failure only; no native write or human receipt.
with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'}):
    original=store.prepare('notes_change',{'account':account,'context':recovery.engine.context(True),'changes':[item]});store.consume(original['plan_id'],account)
try:adapter.apply(item)
except Exception:pass
store.finish(original['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id':original['plan_id']}))
module.NotesCreationRecovery=lambda supplied_account:recovery
from icloud_for_agents.server import main
main()
