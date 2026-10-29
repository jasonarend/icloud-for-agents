"""Exact custom grocery-category creation with bounded source preservation."""
from copy import deepcopy
import uuid
from .core import SafetyError,canonical
from .native_once import planning_access
from .native_read import native_config
from .native_reminder_moves import check_item_graph
from .native_reminder_template_store import identity

COVERAGE='private_grocery_list_complete_bounded_root_tasks_order_and_category_inventory'
RECOVERY_EFFECT='Only this private grocery list metadata, flags, bounded root-task snapshots/manual order and category inventory are compared. Unexposed classifier state, section ordering, delayed effects and cloud synchronization remain unproven. The preassigned category ID is absent from that observed inventory, not proven absent globally. No replay.'

def private_scope(adapter,source,listing):
 config=native_config('reminders')
 if config.get('advanced_backend')!='reminderkit':raise SafetyError('Grocery categories require the explicitly selected ReminderKit helper')
 matches=[v for v in adapter.lists()if v['id']==listing and v['source_id']==source]
 if len(matches)!=1 or matches[0].get('writable')is not True:raise SafetyError('Select an exact writable grocery list in the selected source')
 role=adapter.reader.read('list_features',list_id=listing)
 if(role.get('source_id')!=source or role.get('list_id')!=listing or role.get('data_mutated')is not False or role.get('grocery_categorization')is not True or any(role.get(k)is not False for k in('grocery_shopping_session','is_group','shared','shared_to_me'))):raise SafetyError('Select a private grocery list without shopping-session or group state')
 return {'list':matches[0],'features':role}

def snapshot(adapter,item):
 source=item['source_id'];listing=item['list_id'];scope=private_scope(adapter,source,listing);graph=adapter.reader.read('list_order',list_id=listing);sections=adapter.reader.read('list_sections',list_id=listing)
 if(graph.get('source_id')!=source or graph.get('list_id')!=listing or len(graph['nodes'])>20 or any(v['parent_id']is not None for v in graph['nodes'])or sections.get('source_id')!=source or sections.get('list_id')!=listing or not isinstance(sections.get('sections'),list)or len(sections['sections'])>100):raise SafetyError('Grocery category changes require at most twenty complete root tasks and a bounded category inventory')
 known={v['id']:v for v in sections['sections']};items=[]
 for node in graph['nodes']:
  value=adapter.snapshot({'operation':'update_reminder','source_id':source,'id':node['id'],'fields':{}});admission=deepcopy(value);admission['native_metadata'].update(section=None,section_status='provider_not_reported');check_item_graph(admission,graph,node['id'])
  section=value['native_metadata'].get('section')
  if(value['item'].get('completed')is not False or value['native_metadata'].get('child_ids')!=[]or section is not None and known.get(section.get('id'))!=section):raise SafetyError('Only incomplete nonrecurring grocery root tasks with exact category relationships are admitted')
  items.append(value)
 if private_scope(adapter,source,listing)!=scope or adapter.reader.read('list_order',list_id=listing)!=graph or adapter.reader.read('list_sections',list_id=listing)!=sections:raise SafetyError('Grocery source changed during review')
 value={**scope,'graph':graph,'sections':sections,'items':items}
 if len(canonical(value))>100000:raise SafetyError('Grocery preservation observations exceed the verified review bound')
 return value

def prepare(adapter,change):
 if(not isinstance(change,dict)or set(change)!={'operation','source_id','list_id','fields'}or change.get('operation')!='create_grocery_category'or change['source_id']not in planning_access('reminders')['source_ids']or not isinstance(change['fields'],dict)or set(change['fields'])!={'title'}):raise SafetyError('Create one custom grocery category with exact source_id/list_id and fields.title')
 identity(change['source_id']);identity(change['list_id']);title=change['fields']['title']
 if not isinstance(title,str)or not title.strip()or len(title)>1000 or len(title.encode('utf-8',errors='replace'))>4000 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in title):raise SafetyError('Use a bounded nonempty category title without controls')
 item=deepcopy(change);item['id']=str(uuid.uuid4()).upper();item['before']=snapshot(adapter,item)
 if len(item['before']['sections']['sections'])>=100 or any(v['id']==item['id']or v['title']==title for v in item['before']['sections']['sections']):raise SafetyError('Category title/identity already exists or inventory is full')
 item.update(fidelity_verified=True,effects={'change':'Create one custom category in this exact private grocery list; no task is assigned, edited or classified.',
  'preservation':'Review every bounded root task, current native order and category inventory. Stage one exact new category and only the parent list section-order refresh flag; preserve account storage and track no Reminder changes.',
  'limits':'Automatic suggestions, predefined categories, exact section display order, unexposed metadata and synchronization remain unverified. Category deletion is not included.', 'quota_objects':1})
 return item

def expected_snapshot(item):
 value=deepcopy(item['before']);value['sections']['sections']=sorted(value['sections']['sections']+[{'id':item['id'],'title':item['fields']['title']}],key=lambda v:v['id']);return value

def record_matches(item,observed):
 try:return(item['operation']=='create_grocery_category'and observed.get('state')=='custom_grocery_category_created'and observed.get('id')==item['id']and observed.get('source_id')==item['source_id']and observed.get('snapshot')==expected_snapshot(item))
 except(KeyError,TypeError,AttributeError,ValueError):return False

def expected_observation(adapter,item):
 observed={'state':'custom_grocery_category_created','source_id':item['source_id'],'id':item['id'],'snapshot':snapshot(adapter,item)}
 if not record_matches(item,observed):raise SafetyError('Exact custom grocery category and preserved source state did not verify')
 return observed

def verify(adapter,item,outcome):
 if any(outcome.get(k)!=item[k]for k in('source_id','id'))or outcome.get('list_id')!=item['list_id']:return False
 expected_observation(adapter,item);return True
