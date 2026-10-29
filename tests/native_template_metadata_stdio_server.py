"""Synthetic selected template with an existing passive sharing link; no network."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic template server forbids external effects')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from test_native_template_metadata import SOURCE,TEMPLATE,result,link
from icloud_for_agents import credentials,native_read
class Reader:
 def read(self,action,**fields):
  assert action=='template_metadata'
  from icloud_for_agents.core import SafetyError
  if fields['source_id']!=SOURCE or fields['id']!=TEMPLATE:raise SafetyError('Foreign fixture')
  value=result(fields['include_public_link']);value['public_link_present']=True
  if fields['include_public_link']:value['public_link']=link()
  return value
native_read.NativeReader=lambda name:Reader();credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.server import main
main()
