"""Fixed OCR MCP orchestration over a synthetic exported image and recognizer."""
import atexit,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('Synthetic OCR server forbids network/helpers')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from test_native_note_ocr import Reader
from icloud_for_agents import native_note_ocr as module,credentials
reader=Reader();module.NativeReader=lambda name:reader;module.bundle_fingerprint=lambda path:'synthetic-helper'
module.image_processor=lambda path:'/synthetic/processor'
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
from icloud_for_agents.server import main
main()
