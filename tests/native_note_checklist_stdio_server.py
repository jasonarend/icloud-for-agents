"""Production checklist read tool over one isolated synthetic content store."""
import atexit,json,os,sys
from pathlib import Path
blocked=[];audit_path=Path(os.environ['BRIDGE_TEST_AUDIT'])
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic checklist server forbids network/helpers')
sys.addaudithook(audit);atexit.register(lambda:audit_path.write_text(json.dumps(blocked)))
from test_native_note_checklist import ChecklistStoreTests
from icloud_for_agents import credentials,native_note_checklist
original_env=dict(os.environ);fixture=ChecklistStoreTests();fixture.setUp();os.environ.update(original_env);atexit.register(fixture.doCleanups)
native_note_checklist.NativeReader=lambda name:fixture.reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.server import main
main()
