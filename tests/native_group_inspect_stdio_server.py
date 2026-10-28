"""Real MCP transport with synthetic scoped groups and no OS or network access."""
import os,sys
from pathlib import Path
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(AssertionError('No network or helpers')) if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'} else None)
from icloud_for_agents.native_read import NativeReader
SOURCE='11111111-1111-4111-8111-111111111111'
ROOT='22222222-2222-4222-8222-222222222222'
GROUP='33333333-3333-4333-8333-333333333333'
LEAF='44444444-4444-4444-8444-444444444444'
def dispatch(self,request,config):
    assert self.name=='reminders' and request['action']=='group_lists' and request['source_ids']==[SOURCE] and request['source_id']==SOURCE
    group=request['group_id']
    rows=([{'id':GROUP,'title':'Synthetic group','is_group':True}] if group==ROOT else [{'id':LEAF,'title':'Synthetic list','is_group':False}] if group==GROUP else [])
    return {'protocol_version':1,'source_id':SOURCE,'group_id':group,'lists':rows,'membership_complete':False,'ordering_verified':False,'data_mutated':False,'backend':'reminderkit-group-lists-v1'}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
