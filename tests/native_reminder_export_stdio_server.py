"""Real MCP server with a synthetic EventKit observation and no OS access."""
import sys
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(AssertionError('No native helper or network'))
                 if event in {'socket.connect','socket.connect_ex','socket.bind','socket.getaddrinfo','subprocess.Popen'} else None)
import platform
from icloud_for_agents.native_read import NativeReader
platform.system=lambda:'Darwin'
row={'id':'task','source_id':'selected','list_id':'list','title':'Synthetic title','notes':'Synthetic body','completed':False,
     'priority':0,'due':None,'start':None,'created':None,'modified':None,'completion_date':None,'url':None,'location':None,
     'has_recurrence':False,'has_alarms':False,'recurrence_rules':[],'alarms':[],'public_metadata_complete':True,
     'content_complete':False,'omitted_features':['native_tags','hierarchy','attachments','advanced_metadata']}
def dispatch(self,request,config):
    if request.get('action')!='query' or request.get('list_id')!='list':raise AssertionError('Unexpected native request')
    return {'protocol_version':1,'items':[row],'done':True,'next_offset':1,'pagination_consistency':'live_offset_may_shift'}
NativeReader._dispatch=dispatch
from icloud_for_agents.server import main
main()
