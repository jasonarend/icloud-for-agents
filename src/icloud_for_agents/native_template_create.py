"""Create a private template from a bounded exact ordinary Reminder list."""
from copy import deepcopy
import uuid
from .core import SafetyError
from .native_once import planning_access
from .native_read import native_config,validate_template_list
from .native_reminder_moves import list_scope,check_item_graph
from .native_reminder_sections import sections
from .native_reminder_template_store import outline,identity
from .native_template_item_details import inspect as item_details
from .native_template_metadata import validate

COVERAGE='bounded_template_inventory_and_exact_plain_source_list_graph_sections_and_exposed_items'
RECOVERY_EFFECT='Only the bounded template inventory and reviewed source list, graph, sections and exposed Reminder snapshots are compared. The preassigned template ID is absent from this observed inventory, not proven absent globally. Delayed native copies, hidden content and cloud effects remain unproven. No replay.'

def inventory(adapter,source):
 value=adapter.reader.read('templates',source_id=source,limit=100,offset=0);validate_template_list(value,source,100,0)
 if value['done']is not True or value['next_offset']!=len(value['templates']):raise SafetyError('Template creation requires a complete bounded inventory with capacity')
 rows=value['templates']
 if rows!=sorted(rows,key=lambda row:row['id'])or any(str(uuid.UUID(row['id'])).upper()!=row['id']or len(row['title'].encode())>4000 or any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in row['title'])for row in rows):raise SafetyError('Template inventory identities or titles are incomplete')
 return rows

def snapshot(adapter,item):
 source=item['source_id'];listing=item['list_id'];config=native_config('reminders')
 if config.get('metadata_backend')!='reminders-store-readonly':raise SafetyError('Template creation requires the explicit optional read-only store guard for content verification')
 scope=list_scope(adapter,source,listing);graph=adapter.reader.read('list_hierarchy',list_id=listing);section=sections(adapter,source,listing);templates=inventory(adapter,source)
 if graph['source_id']!=source or graph['list_id']!=listing or not 1<=len(graph['nodes'])<=19 or section['sections']:raise SafetyError('Select a private ordinary unsectioned list with one to nineteen incomplete root reminders')
 items=[];titles=set()
 for node in graph['nodes']:
  value=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':node['id'],'fields':{}});check_item_graph(value,graph,node['id']);data=value['item'];native=value['native_metadata'];title=data.get('title')
  if(node['parent_id']is not None or node['completed']is not False or data.get('completed')is not False or data.get('due')is not None or data.get('start')is not None or data.get('url')is not None or data.get('alarms')!=[]or data.get('has_alarms')is not False or data.get('has_recurrence')is not False or data.get('location')is not None or native.get('flagged')is not False or native.get('flag_state')!=0 or native.get('tags')!=[]or native.get('tags_complete')is not True or native.get('early_alerts')!=[]or native.get('early_payload_fingerprint')is not None or value.get('urgent')is not False or native.get('has_subtasks')is not False or native.get('child_ids')!=[]or not isinstance(title,str)or not title.strip()or title in titles or len(title.encode())>4000 or data.get('notes')!=''):raise SafetyError('Template source requires unique bounded plain root tasks without scheduling, links, flags, tags or descendants')
  titles.add(title);items.append(value)
 if adapter.reader.read('list_hierarchy',list_id=listing)!=graph or sections(adapter,source,listing)!=section or inventory(adapter,source)!=templates:raise SafetyError('Template inventory or source list changed during review')
 return {'source_scope':scope,'source_graph':graph,'sections':section,'items':items,'templates':templates}

def prepare(adapter,change):
 if(not isinstance(change,dict)or set(change)!={'operation','source_id','list_id','fields'}or change.get('operation')!='create_template'or change['source_id']not in planning_access('reminders')['source_ids']or not isinstance(change['fields'],dict)or set(change['fields'])!={'title'}):raise SafetyError('Create one template with exact source_id, source list_id and fields.title')
 identity(change['source_id']);identity(change['list_id']);title=change['fields']['title']
 if not isinstance(title,str)or not title.strip()or len(title)>1000 or len(title.encode('utf-8',errors='replace'))>4000 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in title):raise SafetyError('Use a bounded nonempty template title without controls')
 item=deepcopy(change);item['id']=str(uuid.uuid4()).upper();item['before']=snapshot(adapter,item)
 if len(item['before']['templates'])>=100 or any(v['id']==item['id']or v['title']==title for v in item['before']['templates']):raise SafetyError('Template title or proposed identity already exists')
 item.update(fidelity_verified=True,effects={'change':'Create one private saved template from every reviewed incomplete root task in this exact list, with completed tasks excluded. No list is instantiated.',
  'source':'Private ordinary unsectioned list; one to nineteen unique-title incomplete roots with empty notes. Scheduling, tags, flags, urgent state, known attachments and assignments are refused; unexposed rich document representation is unverified. Native source content is copied by Apple; no live task is edited.',
  'verification':'Independent exact saved-item text and priority readback, bounded template inventory, native configuration and unchanged source observations. Manual display order, complete rich representation and cloud synchronization are not certified.',
  'cleanup':'Creates a persistent template; template deletion, sharing and instantiation are not included. Uncertain creation is never replayed.', 'quota_objects':charge(item)})
 return item

def charge(item):
 try:
  rows=item['before']['items'];nodes=item['before']['source_graph']['nodes'];count=len(rows)
  if not 1<=count<=19 or count!=len(nodes)or len({v['item']['id']for v in rows})!=count or {v['item']['id']for v in rows}!={v['id']for v in nodes}:raise ValueError()
  return count+1
 except(KeyError,TypeError,ValueError,AttributeError):raise SafetyError('Template quota requires one template plus every bound saved-item copy')from None

def content_matches(item,details):
 expected=sorted([(v['item']['title'],v['item']['notes'],v['item']['priority'])for v in item['before']['items']])
 actual=[]
 for row in details:
  if row.get('parent_item_id')is not None or row.get('representation_complete')is not False or row.get('restorable')is not False or row.get('native_checklist_items_omitted')!=0 or row.get('omitted_metadata_keys')!=[]:return False
  native=row.get('native_metadata_uninterpreted',{})
  if native.get('flagged')not in(False,0)or native.get('recurrenceRules')!=[]or native.get('alarmTriggers')!={'dateTriggers':[],'timeIntervalTriggers':[],'locationTriggers':[],'vehicleTriggers':[]}:return False
  if row.get('source_id')!=item['source_id']or row.get('template_id')!=item['id']:return False
  actual.append((row.get('title'),row.get('notes'),row.get('priority')))
 return sorted(actual)==expected and len({v['item_id']for v in details})==len(details)

def record_matches(item,observed):
 try:
  before=item['before'];after=observed['snapshot'];expected=deepcopy(before);expected['templates']=sorted(before['templates']+[{'id':item['id'],'title':item['fields']['title']}],key=lambda v:v['id']);metadata=observed['metadata'];outline=observed['outline'];detail=observed['details'];config=metadata['configuration']
  return(item['operation']=='create_template'and observed.get('state')=='saved_template_created'and observed.get('id')==item['id']and observed.get('source_id')==item['source_id']and after==expected and metadata['id']==item['id']and metadata['source_id']==item['source_id']and metadata['title']==item['fields']['title']and metadata['public_link_present']is False and metadata['minimum_supported_version']==0 and config in(None,{'source_list_id':item['list_id'],'save_completed':False})and observed['sections']['sections']==[]and outline['title']==item['fields']['title']and outline['done']is True and outline['total_items']==len(before['items'])and len(detail)==len(before['items'])and{v['item_id']for v in detail}=={v['item_id']for v in outline['items']}and content_matches(item,detail))
 except(KeyError,TypeError,AttributeError,ValueError):return False

def expected_observation(adapter,item):
 source=item['source_id'];identity=item['id'];metadata=validate(adapter.reader.read('template_metadata',source_id=source,id=identity,include_public_link=False),source,identity,False)
 content=outline(source,identity,limit=20,offset=0,reader=adapter.reader)
 details=[item_details(source,identity,row['item_id'],reader=adapter.reader)for row in content['items']]
 value={'state':'saved_template_created','source_id':source,'id':identity,'snapshot':snapshot(adapter,item),'metadata':metadata,'sections':adapter.reader.read('template_sections',source_id=source,id=identity),'outline':content,'details':details}
 if not record_matches(item,value):raise SafetyError('Created template configuration/content or unchanged source did not independently verify; do not retry')
 return value

def verify(adapter,item,outcome):
 if any(outcome.get(k)!=item[k]for k in('source_id','id')):return False
 expected_observation(adapter,item);return True
