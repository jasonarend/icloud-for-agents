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
from icloud_for_agents import credentials,native_move_recovery as module
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import digest
from test_native_move_recovery import Adapter
folder_mode=os.environ.get('BRIDGE_TEST_FOLDER_MODE')=='1'
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
account='synthetic@example.com';store=PlanStore();adapter=Adapter();recovery=module.NotesMoveRecovery(account,store,adapter)
item={'operation':'move_note','source_id':'selected','folder_id':'source','id':'note','fields':{'title':'Synthetic','destination_folder_id':'destination'},'before':{'folder':adapter.private_folder('source','selected'),'destination':adapter.private_folder('destination','selected'),'note':dict(adapter.note),'attachments':list(adapter.attachments)},'fidelity_verified':True,'effects':{}}
if folder_mode:
    from test_native_folder_observation import Adapter as FolderAdapter,item as folder_item,A,S
    from icloud_for_agents import native_folder_observation
    adapter=FolderAdapter();item=folder_item();recovery=module.NotesMoveRecovery(account,store,adapter)
    native_folder_observation.empty_folder_membership=lambda identity,sources:{'folder_id':A,'source_id':S,'live_notes':0,'live_child_folders':0,'content_read':False,'database_writes':False}
# Historical test fixture only. No original provider write or real human receipt.
with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'}):
    original=store.prepare('notes_change',{'account':account,'context':recovery.engine.context(True),'changes':[item]});store.consume(original['plan_id'],account)
store.finish(original['plan_id'],{'status':'failed_or_uncertain','phase':'mutation','attempted':[],'completed':[],'do_not_retry':True})
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id':original['plan_id'],'original_result_sha256':digest(store.read(store.path(original['plan_id'],'.result')))}))
module.completed_exchange_evidence=lambda request,**kwargs:{'request_id':'request-synthetic','request_sha256':digest(request),'helper_response_sha256':'a'*64,'helper_outcome':'error_response','mutation_dispatched':True}
module.NotesMoveRecovery=lambda supplied_account:recovery
from icloud_for_agents.server import main
main()
