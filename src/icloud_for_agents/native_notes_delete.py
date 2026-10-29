"""Recoverable Notes removal by moving an existing object to verified system Trash.

Never calls Notes' delete command. Empty/inaccessible recovery destinations are
refused rather than creating a marker, changing another note or deleting forever.
"""
from .core import SafetyError
from .native_once import planning_access,selected_read_access
from .native_read import native_config
from .native_notes_restore import CONTENT_KEYS,stable_roles


def endpoints(adapter,source,origin,*,require_visible=True):
    if source not in selected_read_access('notes')['source_ids']:raise SafetyError('Deletion source is outside selected Notes accounts')
    if native_config('notes').get('metadata_backend')!='notes-store-readonly':raise SafetyError('Recoverable deletion requires explicitly selected read-only Notes folder metadata')
    from .native_note_store import recovery_folder
    from .native_notes_restore import endpoints as restore_endpoints
    recovery=recovery_folder(source);trash=recovery['folder_id']
    _,ordinary,roles=restore_endpoints(adapter,source,trash,origin,resolve_origin=False)
    if require_visible:
        try:target=adapter.private_folder(trash,source)
        except SafetyError:raise SafetyError('Recently Deleted is empty or unavailable to Notes automation; no deletion. Use Notes itself or an approved move to an ordinary holding folder.') from None
        if target.get('title')!=recovery['title'] or target.get('parent_id')!=source or target.get('private_ancestors'):raise SafetyError('System recovery folder scripting and saved metadata disagree')
    else:target={'id':trash,'source_id':source,'title':recovery['title'],'parent_id':source,'shared':False}
    return ordinary,target,roles


def snapshot(adapter,item):
    from .native_note_store import note_location
    start,target,roles=endpoints(adapter,item['source_id'],item['folder_id'])
    if item['fields'].get('destination_folder_id')!=target['id']:raise SafetyError('System recovery destination changed')
    observed=adapter.note_snapshot(item,rewrite=False,verify_folder_role=False);location=note_location(item['id'],[item['source_id']])
    if observed['folder']!=start or location!={'id':item['id'],'folder_id':item['folder_id'],'source_id':item['source_id'],'content_read':False,'database_writes':False}:raise SafetyError('Delete requires the exact saved native note in its reviewed ordinary source folder')
    return dict(observed,destination=target,roles=roles,persisted_location=location)


def prepare(adapter,change):
    if (not isinstance(change,dict) or set(change)!={'operation','source_id','folder_id','id','fields'} or change.get('operation')!='delete_note'
            or change['source_id'] not in planning_access('notes')['source_ids']):raise SafetyError('Select an exact note in an ordinary folder of the selected Notes source')
    if change['fields']!={'recoverable':True} or type(change['fields'].get('recoverable')) is not bool:raise SafetyError('Delete requires exactly fields.recoverable true; permanent deletion is unavailable')
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (change['folder_id'],change['id'])):raise SafetyError('Delete requires exact saved note and folder IDs')
    _,target,_=endpoints(adapter,change['source_id'],change['folder_id'])
    item=dict(change,fields={'recoverable':True,'destination_folder_id':target['id']});item['before']=snapshot(adapter,item);item['fields']['title']=item['before']['note']['title'];item.update(fidelity_verified=True,
        effects={'deletion':'Move only this existing native note to the verified selected-account system Recently Deleted folder. Never permanently delete or empty Trash.',
                 'recovery':'Apple controls recovery retention and synchronization. A separate approved restore_note can move it to an ordinary private folder.',
                 'content':'Preserve native ID, exported content, creation metadata and exposed attachment identities without rewriting the body.',
                 'destination':'An empty or inaccessible system Trash refuses before dispatch; no placeholder note or alternate deletion route is created.'})
    return item


def observe(adapter,item):
    from .native_note_store import note_location
    source=item['source_id'];origin=item['folder_id'];destination=item['fields']['destination_folder_id']
    start,target,roles=endpoints(adapter,source,origin,require_visible=False)
    if (target['id']!=destination or start!=item['before']['folder'] or stable_roles(roles)!=stable_roles(item['before']['roles'])):raise SafetyError('Source or system recovery role, title or ancestry changed')
    location=note_location(item['id'],[source]);actual=location['folder_id']
    if location['source_id']!=source or actual not in {origin,destination}:raise SafetyError('Note is outside its reviewed ordinary/recovery folders')
    observed=adapter.note_snapshot(dict(item,folder_id=actual),rewrite=False,verify_folder_role=False)
    if any(observed['note'].get(key)!=item['before']['note'].get(key) for key in CONTENT_KEYS) or observed['attachments']!=item['before']['attachments']:raise SafetyError('Recoverable deletion identity, observable content or attachments changed')
    return observed,location,roles


def verify(adapter,item,outcome):
    if outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('folder_id')!=item['fields']['destination_folder_id']:return False
    _,location,_=observe(adapter,item)
    return location['folder_id']==item['fields']['destination_folder_id']
