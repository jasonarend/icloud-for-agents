"""Review a deliberately lossy template text copy using existing Reminder creation."""
from .core import SafetyError,digest
from .native_read import native_config
from .native_once import selected_read_access
from .native_reminder_template_store import outline,identity
from .native_template_item_details import inspect as details

def prepare(engine,source_id,template_id,list_id,*,accept_omissions,grant_id=None):
 if grant_id is not None and(not isinstance(grant_id,str)or not 1<=len(grant_id)<=1000):raise SafetyError('Use one exact active copy scenario grant')
 if engine.name!='reminders'or accept_omissions is not True:raise SafetyError('Explicitly accept that only title, notes text and priority will be copied into new incomplete reminders')
 for value in(source_id,template_id,list_id):identity(value)
 access=selected_read_access('reminders');config=native_config('reminders')
 before=outline(source_id,template_id,limit=20,offset=0,reader=engine.adapter.reader)
 if before['done']is not True or type(before['total_items'])is not int or not 1<=before['total_items']<=10 or len(before['items'])!=before['total_items']or len({v['item_id']for v in before['items']})!=before['total_items'] or any(v['parent_item_id']is not None for v in before['items']):raise SafetyError('Template text copies require one complete page of one to ten root saved items; nested templates need a separate hierarchy policy')
 rows=[details(source_id,template_id,v['item_id'],reader=engine.adapter.reader)for v in before['items']]
 if outline(source_id,template_id,limit=20,offset=0,reader=engine.adapter.reader)!=before or access!=selected_read_access('reminders')or config!=native_config('reminders'):raise SafetyError('Saved template or access changed while capturing the copy text')
 changes=[]
 for expected,row in zip(before['items'],rows):
  if(row.get('item_id')!=expected['item_id']or row.get('title')!=expected['title']or row.get('priority')!=expected['priority']or not isinstance(row.get('title'),str)or not isinstance(row.get('notes'),str)or row.get('source_id')!=source_id or row.get('template_id')!=template_id or row.get('parent_item_id')is not None or type(row.get('priority'))is not int or row['priority']not in(0,1,5,9)):raise SafetyError('Exact saved-item identity, hierarchy or priority cannot be copied safely')
  changes.append({'operation':'create_reminder','source_id':source_id,'list_id':list_id,'fields':{key:row[key]for key in('title','notes','priority')}})
 review={'operation':'create_lossy_copies_from_user_supplied_observation','accepted_omissions':True,'source_format':'observed_saved_template_text_projection',
  'source_template':{'source_id':source_id,'template_id':template_id,'outline_sha256':digest(before),'saved_item_ids':[v['item_id']for v in rows],'details_sha256':digest(rows)},'item_count':len(rows),'include_dates':False,'include_urls':False,'include_alarms':False,
  'effects':['Create new incomplete root reminders in the exact reviewed existing destination list. Only title, notes text and priority are copied; saved template item IDs are not live Reminder IDs.',
   'Native template instantiation is not used. Tags, flags, dates, alarms, recurrence, links, attachments, rich objects, original identity, completion, timestamps and native ordering are omitted. No interpreted native scheduling values are used.',
   'The approved text is a captured local observation; subsequent template edits are not included or replayed. This is not a complete backup or restoration. Duplicates may result.',
   'The template stays unchanged. To use a new list, separately create that list through its existing approval workflow first, then select its returned exact ID. Copy grants scope only the exact destination and fields.']}
 if grant_id is not None:return engine.prepare_with_grant(grant_id,changes,_review_context=review)
 return engine.prepare_once(changes,_review_context=review)
