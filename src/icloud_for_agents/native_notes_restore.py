"""Restore an exact existing native note from verified system Recently Deleted."""
from .core import SafetyError
from .native_once import planning_access,selected_read_access
from .native_read import native_config

CONTENT_KEYS=('id','title','html','text','created','locked','shared','truncated')


def endpoints(adapter,source,origin,destination,*,resolve_origin=True):
    if source not in selected_read_access('notes')['source_ids']:raise SafetyError('Restoration source is outside selected Notes accounts')
    if native_config('notes').get('metadata_backend')!='notes-store-readonly':raise SafetyError('Restoration requires explicitly selected read-only Notes folder metadata')
    from .native_note_store import recovery_folder,folder_role,parse_identity
    recovery=recovery_folder(source)
    if (recovery.get('folder_id')!=origin or recovery.get('source_id')!=source or recovery.get('native_folder_type')!=1
            or recovery.get('storage_kind')!='special_smart_or_deleted' or recovery.get('marked_for_deletion') is not False
            or recovery.get('has_smart_query') is not False or recovery.get('parent_pk') is not None or origin==destination):raise SafetyError('Restore only from the exact selected account system Recently Deleted folder')
    # Check metadata before scripting, including when the empty Trash is hidden.
    def ordinary(identity):
        role=folder_role(identity,[source])
        if (role.get('folder_id')!=identity or role.get('source_id')!=source or role.get('storage_kind')!='regular'
                or role.get('native_folder_type')!=0 or role.get('marked_for_deletion') is not False or role.get('has_smart_query') is not False):raise SafetyError('Restore destination and ancestors must be ordinary nondeleted nonsmart folders')
        return role
    ordinary(destination);target=adapter.private_folder(destination,source);roles={origin:{key:value for key,value in recovery.items() if key not in {'role','data_mutated'}}}
    for node in [target,*target.get('private_ancestors',[])]:
        role=ordinary(node['id']);parent=node.get('parent_id');pk=None if parent==source else parse_identity(parent,'ICFolder')[1]
        if role.get('title')!=node.get('title') or role.get('parent_pk')!=pk:raise SafetyError('Restore folder title or saved parent differs from scripting')
        roles[node['id']]=role
    if resolve_origin:
        start=adapter.private_folder(origin,source)
        if start.get('parent_id')!=source or start.get('title')!=recovery.get('title') or start.get('private_ancestors'):raise SafetyError('System recovery folder scripting and saved metadata disagree')
    else:start=None
    return start,target,[roles[key] for key in sorted(roles)]


def snapshot(adapter,item):
    from .native_note_store import note_location
    start,target,roles=endpoints(adapter,item['source_id'],item['folder_id'],item['fields']['destination_folder_id'])
    observed=adapter.note_snapshot(item,rewrite=False,verify_folder_role=False);location=note_location(item['id'],[item['source_id']])
    if observed['folder']!=start or location!={'id':item['id'],'folder_id':item['folder_id'],'source_id':item['source_id'],'content_read':False,'database_writes':False}:raise SafetyError('Restore requires the exact saved native note in the selected system recovery folder')
    return dict(observed,destination=target,roles=roles,persisted_location=location)


def prepare(adapter,change):
    if not isinstance(change,dict) or set(change)!={'operation','source_id','folder_id','id','fields'} or change.get('operation')!='restore_note':raise SafetyError('Use exact restore source, recovery folder, note and destination')
    if change['source_id'] not in planning_access('notes')['source_ids']:raise SafetyError('Select an exact Notes source')
    fields=change['fields']
    if not isinstance(fields,dict) or set(fields)!={'destination_folder_id'}:raise SafetyError('Restore requires one exact ordinary destination folder')
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (change['folder_id'],change['id'],fields['destination_folder_id'])):raise SafetyError('Restore requires saved exact note and folder IDs')
    item=dict(change,fields=dict(fields));item['before']=snapshot(adapter,item);item['fields']['title']=item['before']['note']['title'];item.update(fidelity_verified=True,effects={'recovery':'Move this exact existing note out of the verified selected-account system Recently Deleted folder. No permanent deletion.',
        'content':'Preserve native identity, exported content, creation metadata and exposed attachment identities without rewriting the body.',
        'scope':'Only the exact ordinary private destination and its private ancestors; cloud sync and unexposed metadata remain unverified.'})
    return item


def stable_roles(values):return [{key:value for key,value in role.items() if key!='revision'} for role in values]


def observe(adapter,item):
    """Resolve physical placement even when restoring hides an emptied Trash."""
    from .native_note_store import note_location
    source=item['source_id'];origin=item['folder_id'];destination=item['fields']['destination_folder_id']
    _,target,roles=endpoints(adapter,source,origin,destination,resolve_origin=False)
    if stable_roles(roles)!=stable_roles(item['before']['roles']) or target!=item['before']['destination']:raise SafetyError('Recovery folder or destination role, title or ancestry changed')
    location=note_location(item['id'],[source]);actual=location['folder_id']
    if location['source_id']!=source or actual not in {origin,destination}:raise SafetyError('Note is outside its reviewed recovery folders')
    observed=adapter.note_snapshot(dict(item,folder_id=actual),rewrite=False,verify_folder_role=False)
    if any(observed['note'].get(key)!=item['before']['note'].get(key) for key in CONTENT_KEYS) or observed['attachments']!=item['before']['attachments']:raise SafetyError('Restoration identity, observable content or attachments changed')
    return observed,location,roles


def verify(adapter,item,outcome):
    if outcome.get('id')!=item['id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('folder_id')!=item['fields']['destination_folder_id']:return False
    _,location,_=observe(adapter,item)
    return location['folder_id']==item['fields']['destination_folder_id']
