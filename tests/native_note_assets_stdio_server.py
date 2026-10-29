"""Synthetic native-asset observations with actual MCP request/response handling."""
import atexit,copy,json,os,sys
from pathlib import Path
blocked=[]
def audit(event,args):
 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'}:blocked.append(event);raise AssertionError('No network/helper in native-asset protocol fixtures')
sys.addaudithook(audit);atexit.register(lambda:Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps(blocked)))
from icloud_for_agents import credentials,native_note_assets as assets
from icloud_for_agents.core import SafetyError
from test_native_note_assets import UUID,SOURCE,FOLDER,NOTE,ASSET_STORAGE,ACCOUNT_STORAGE,GENERATION,RAW,Reader
credentials.credentials=lambda:(_ for _ in ()).throw(AssertionError('No credentials'))
def state(folder,identity,**kwargs):
 access=assets._access()
 if folder!=FOLDER or identity!=NOTE:raise SafetyError('Synthetic exact native scope mismatch')
 row={'Z_PK':30,'Z_ENT':4,'Z_OPT':1,'ZIDENTIFIER':ASSET_STORAGE,'ZNOTE':20,'ZTYPEUTI':'com.apple.paper.doc.pdf','ZPARENTATTACHMENT':None,'ZFALLBACKPDFGENERATION':GENERATION,'ZFALLBACKPDFCRYPTOINITIALIZATIONVECTOR':0,'ZFALLBACKPDFCRYPTOTAG':0}
 return {'id':NOTE,'folder_id':FOLDER,'source_id':SOURCE,'note_revision':1,'pinned':False,'schema_sha256':'0'*64,'account_storage_id':ACCOUNT_STORAGE,'assets':[row]},access
def pdf(account,asset,generation,maximum):
 assert (account,asset,generation)==(ACCOUNT_STORAGE,ASSET_STORAGE,GENERATION)
 if len(RAW)>maximum:raise SafetyError('Synthetic generated PDF exceeds bound')
 return RAW
assets._state=state;assets._pdf_bytes=pdf;assets.NativeReader=lambda name:Reader()
from icloud_for_agents.server import main
main()
