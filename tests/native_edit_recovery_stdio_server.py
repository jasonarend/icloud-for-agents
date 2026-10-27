"""Production edit-observation MCP handlers with a historical synthetic failure."""
import atexit,json,os,sys
from pathlib import Path
from unittest.mock import patch
blocked=[]
def audit(event,args):
    if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:
        blocked.append(event);raise AssertionError('Synthetic observation server forbids network/helpers')
sys.addaudithook(audit)
atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_edit_recovery as module,native_edit_observation as observation
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import digest
from test_native_edit_observation import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
account='synthetic@example.com';store=PlanStore();adapter=Adapter();recovery=module.NotesEditRecovery(account,store,adapter)
item={'operation':'append_note','source_id':'selected','folder_id':'old','id':'note','fields':{'title':'Synthetic','html':'<div>Synthetic</div><div>new</div>'},'before':{'note':dict(adapter.note),'attachments':[]},'fidelity_verified':True,'effects':{}}
with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'}):
    original=store.prepare('notes_change',{'account':account,'context':recovery.engine.context(True),'changes':[item]});store.consume(original['plan_id'],account)
store.finish(original['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id':original['plan_id'],'original_result_sha256':digest(store.read(store.path(original['plan_id'],'.result')))}))
observation.completed_exchange_evidence=lambda request,**kwargs:{'request_id':'request-synthetic','request_sha256':digest(request),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True}
module.NotesEditRecovery=lambda supplied_account:recovery
from icloud_for_agents.server import main
main()
