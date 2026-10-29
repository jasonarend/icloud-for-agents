"""Rename one exact private grocery category without changing task content."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_grocery_categories import snapshot,private_scope
from .native_reminder_template_store import identity

COVERAGE='private_grocery_category_name_complete_bounded_root_tasks_order_and_category_inventory'
RECOVERY_EFFECT='Only the exact private grocery list metadata, bounded root tasks/manual order and category inventory are compared. Hidden category configuration, section display order, delayed effects and synchronization remain unproven. No replay.'

def prepare(adapter,change):
 if(not isinstance(change,dict)or set(change)!={'operation','source_id','list_id','id','fields'}or change.get('operation')!='rename_grocery_category'or change['source_id']not in planning_access('reminders')['source_ids']or not isinstance(change['fields'],dict)or set(change['fields'])!={'title'}):raise SafetyError('Rename one exact grocery category with source_id/list_id/id and fields.title')
 for key in('source_id','list_id','id'):identity(change[key])
 title=change['fields']['title']
 if not isinstance(title,str)or not title.strip()or len(title)>1000 or len(title.encode('utf-8',errors='replace'))>4000 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in title):raise SafetyError('Use a bounded nonempty category title without controls')
 item=deepcopy(change);item['before']=snapshot(adapter,item);inventory=item['before']['sections']['sections'];selected=[v for v in inventory if v['id']==item['id']]
 if len(selected)!=1 or any(v['title']==title for v in inventory):raise SafetyError('Select an existing category and a different unused title')
 item.update(fidelity_verified=True,effects={'change':'Rename this exact private grocery category; no task or category membership is edited.',
  'preservation':'Verify complete bounded root-task content, native order and categories. Stage only the exact category display name and preserve original category/list/account storage and unrelated trackers.',
  'limits':'Hidden category configuration, section display order, future automatic suggestions and synchronization remain unverified. Category deletion is not included.','quota_objects':1})
 return item

def expected_snapshot(item):
 value=deepcopy(item['before']);matches=[v for v in value['sections']['sections']if v['id']==item['id']]
 if len(matches)!=1:raise SafetyError('Exact reviewed category is unavailable')
 matches[0]['title']=item['fields']['title']
 for observed in value['items']:
  category=observed['native_metadata'].get('section')
  if category is not None and category['id']==item['id']:category['title']=item['fields']['title']
 return value

def record_matches(item,observed):
 try:return(item['operation']=='rename_grocery_category'and observed.get('state')=='grocery_category_renamed'and observed.get('id')==item['id']and observed.get('source_id')==item['source_id']and observed.get('snapshot')==expected_snapshot(item))
 except(KeyError,TypeError,AttributeError,ValueError,SafetyError):return False

def expected_observation(adapter,item):
 value={'state':'grocery_category_renamed','source_id':item['source_id'],'id':item['id'],'snapshot':snapshot(adapter,item)}
 if not record_matches(item,value):raise SafetyError('Exact grocery category title and otherwise preserved source did not verify')
 return value

def verify(adapter,item,outcome):
 if any(outcome.get(k)!=item[k]for k in('source_id','id'))or outcome.get('list_id')!=item['list_id']:return False
 expected_observation(adapter,item);return True
