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
from icloud_for_agents import credentials,native_tools
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from test_native_corrections import Adapter
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
account='synthetic@example.com';store=PlanStore();adapter=Adapter();engine=NativeChanges('reminders',account,adapter,store)
with patch.dict(os.environ,{'ICLOUD_APPROVAL_POLICY':'trusted-client'}):
    original=engine.prepare_once([{'operation':'update_reminder','source_id':'selected','id':'task','fields':{'due':{'year':2026,'month':10,'day':28}}}]);assert engine.execute(original['plan_id'])['status']=='failed_or_uncertain'
adapter.fail=False;native_tools.RemindersCreateAdapter=lambda:adapter
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id':original['plan_id']}))
from icloud_for_agents.server import main
main()
