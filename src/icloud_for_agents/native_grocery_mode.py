"""Gated native grocery categorization flag on one exact empty private list."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_read import native_config
from .native_reminder_template_store import identity

COVERAGE='exact_empty_private_list_metadata_features_graph_and_sections'
RECOVERY_EFFECT='Only this empty private list metadata, grocery/sharing/group feature observations, complete empty graph and empty section inventory are compared. Unexposed categorization state, delayed effects and cloud synchronization remain unproven. No replay.'

def snapshot(adapter,item):
 source=item['source_id'];listing=item['list_id'];config=native_config('reminders')
 if config.get('advanced_backend')!='reminderkit':raise SafetyError('Grocery categorization requires the explicitly selected ReminderKit helper')
 matches=[v for v in adapter.lists()if v['id']==listing and v['source_id']==source]
 if len(matches)!=1 or matches[0].get('writable')is not True:raise SafetyError('Select an exact writable list in the selected source')
 role=adapter.reader.read('list_features',list_id=listing)
 if(role.get('source_id')!=source or role.get('list_id')!=listing or role.get('data_mutated')is not False or type(role.get('grocery_categorization'))is not bool or any(role.get(k)is not False for k in('grocery_shopping_session','is_group','shared','shared_to_me'))):raise SafetyError('Grocery categorization currently requires an empty private nongroup list without shopping-session state')
 graph=adapter.reader.read('list_hierarchy',list_id=listing);sections=adapter.reader.read('list_sections',list_id=listing)
 if(graph.get('source_id')!=source or graph.get('list_id')!=listing or graph.get('nodes')!=[]or sections.get('source_id')!=source or sections.get('list_id')!=listing or sections.get('sections')!=[]):raise SafetyError('Only complete empty reminder and section observations are admitted')
 if adapter.reader.read('list_features',list_id=listing)!=role or adapter.reader.read('list_hierarchy',list_id=listing)!=graph or adapter.reader.read('list_sections',list_id=listing)!=sections:raise SafetyError('List role or empty inventory changed during review')
 return {'list':matches[0],'features':role,'graph':graph,'sections':sections}

def prepare(adapter,change):
 if(not isinstance(change,dict)or set(change)!={'operation','source_id','list_id','fields'}or change.get('operation')!='set_grocery_categorization'or change['source_id']not in planning_access('reminders')['source_ids']or not isinstance(change['fields'],dict)or set(change['fields'])!={'enabled'}or type(change['fields']['enabled'])is not bool):raise SafetyError('Select exact source_id/list_id and one explicit fields.enabled Boolean')
 identity(change['source_id']);identity(change['list_id']);item=deepcopy(change);item['id']=item['list_id'];item['before']=snapshot(adapter,item)
 if item['before']['features']['grocery_categorization']is item['fields']['enabled']:raise SafetyError('List already has the requested categorization flag')
 item.update(fidelity_verified=True,effects={'change':'Change only the native grocery categorization flag on this exact empty private list. No task or section content is edited.',
  'scope':'No shopping-session state, sharing, groups, populated lists or sections. Native staging must equal a complete storage copy with only shouldCategorizeGroceryItems changed; account storage stays unchanged.',
  'limits':'This flag alone does not establish grocery UI type, working automatic category suggestions, locale, manual overrides or shopping-session support. Apple controls future categorization and synchronization.',
  'quota_objects':1})
 return item

def record_matches(item,observed):
 try:
  expected=deepcopy(item['before']);expected['features']['grocery_categorization']=item['fields']['enabled']
  return(item['operation']=='set_grocery_categorization'and observed.get('state')=='grocery_categorization_changed'and observed.get('source_id')==item['source_id']and observed.get('id')==item['id']and observed.get('snapshot')==expected)
 except(KeyError,TypeError,AttributeError):return False

def expected_observation(adapter,item):
 observed={'state':'grocery_categorization_changed','source_id':item['source_id'],'id':item['id'],'snapshot':snapshot(adapter,item)}
 if not record_matches(item,observed):raise SafetyError('Exact grocery flag and otherwise unchanged empty list observations did not verify')
 return observed

def verify(adapter,item,outcome):
 if any(outcome.get(k)!=item[k]for k in('source_id','id')):return False
 expected_observation(adapter,item);return True
