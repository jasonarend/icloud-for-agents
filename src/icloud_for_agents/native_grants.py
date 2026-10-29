"""Reviewed, revocable native scenario authority with exact selectors and audit.

An active grant substitutes for per-batch approval only inside its reviewed scope.
It never enables services, changes configuration, approves OS consent or bypasses
adapter fidelity checks. Private files are a same-owner workflow boundary.
"""
from contextlib import contextmanager
import fcntl
import math
import os
import stat
from .core import SafetyError,digest
from .native_once import selected_read_access
from .native_read import native_config

FIELDS={
 'notes':{'copy_observation':{'title','body','format'},'create_note':{'title','body','format'},'append_note':{'body','format'},'prepend_note':{'body','format'},'replace_text':{'find','replacement','expected_matches'},
          'update_note':{'title','body','format'},'move_note':{'destination_folder_id'},'restore_note':{'destination_folder_id'},'delete_note':{'recoverable'},'create_folder':{'title'},'rename_folder':{'title'}},
 'reminders':{'add_image_attachment':{'data_base64'},'add_file_attachment':{'uti','data_base64','allow_mcp_only'},'rename_grocery_category':{'title'},'create_grocery_category':{'title'},'set_grocery_categorization':{'enabled'},'set_grocery_category':{'category_id'},'set_section':{'section_id'},'create_template':{'title'},'create_smart_list':{'title','list_ids'},'set_smart_list_sources':{'list_ids'},'rename_template':{'title'},'rename_smart_list':{'title'},'delete_reminder':{'recoverable','include_descendants'},'restore_reminder':{'deletion_plan_id','deletion_index','list_id','include_descendants'},'copy_observation':{'title','notes','priority','due','start','url','alarms'},'create_subtask':{'title'},'create_section':{'title'},'rename_section':{'title'},'create_reminder':{'title','notes','priority','due','start','url','alarms','location_alarms','recurrence'},'update_reminder':{'title','notes','priority','due','start','url','alarms','location_alarms'},
              'set_completed':{'completed'},'set_flagged':{'flagged'},'set_urgent':{'urgent'},'add_early':{'unit','count'},'clear_early':{'clear'},'remove_tag':{'tag_id'},'add_tag':{'tag_name'},'replace_tag':{'tag_id','tag_name'},'move_reminder':{'list_id'},'create_list':{'title'},'rename_list':{'title'},'set_list_color':{'color'},'reparent_reminder':{'parent_id','anchor_id','placement'},'reorder_group':{'anchor_id','placement'},'group_list':{'group_id','allow_nonempty'},'ungroup_list':{'anchor_id','placement'},'rename_group':{'title'},'create_group':{'title'},'reorder_reminder':{'anchor_id','placement'},'set_list_badge':{'badge'},'set_list_pinned':{'pinned'},'set_recurrence':{'recurrence','recurrence_scope'}}}


def exact(value,limit=1000):
    return isinstance(value,str) and 1<=len(value)<=limit and value!='*' and not any(ord(c)<32 for c in value)


def validate_scope(name,scope,now):
    if not isinstance(scope,dict) or set(scope)!={'scenario','rules','max_batch','max_24h','expires'}:
        raise SafetyError('Use an exact native grant schema')
    if not exact(scope['scenario'],500):raise SafetyError('A bounded scenario reference is required')
    if type(scope['max_batch']) is not int or not 1<=scope['max_batch']<=20:
        raise SafetyError('Native standing batch limit must be 1..20')
    if scope['max_24h'] is not None and (type(scope['max_24h']) is not int or scope['max_24h']<scope['max_batch']):
        raise SafetyError('Invalid optional rolling 24-hour item cap')
    expiry=scope['expires']
    if expiry is not None and (type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry<=now):
        raise SafetyError('Native grant is expired or has an invalid lifetime')
    rules=scope['rules']
    if not isinstance(rules,list) or not 1<=len(rules)<=50:raise SafetyError('Use 1..50 exact native grant rules')
    seen=set()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)!={'operation','source_id','container_id','fields','destinations'}:
            raise SafetyError('Unknown native grant rule fields')
        op=rule['operation'];fields=rule['fields'];dest=rule['destinations']
        if not isinstance(op,str) or op not in FIELDS[name] or not exact(rule['source_id']) or not exact(rule['container_id']):
            raise SafetyError('Native grant operation/source/container is unsupported')
        if (not isinstance(fields,list) or not fields or any(not isinstance(f,str) for f in fields)
                or len(set(fields))!=len(fields) or set(fields)-FIELDS[name][op]):
            raise SafetyError('Native grant field scope must explicitly select supported fields')
        if 'location_alarms' in fields and 'alarms' not in fields:raise SafetyError('Location-alarm authority requires explicit complete alarm-set authority')
        if (not isinstance(dest,list) or len(dest)>20 or any(not exact(d) for d in dest)
                or len(set(dest))!=len(dest) or bool(dest)!=(op in {'rename_grocery_category','create_smart_list','set_smart_list_sources','move_note','restore_note','move_reminder','restore_reminder','create_subtask','rename_section','reorder_reminder','group_list','ungroup_list','reorder_group','set_grocery_category','set_section','reparent_reminder'})):
            raise SafetyError('Moves require exact destinations, subtask creation exact parents and section renames exact section IDs; other rules cannot grant them')
        if op=='rename_section':
            import uuid
            try:
                normalized=[str(uuid.UUID(value)).upper() for value in dest]
                if any(str(uuid.UUID(value)).upper()!=value.upper() for value in dest) or len(set(normalized))!=len(normalized):raise ValueError()
            except (ValueError,AttributeError):raise SafetyError('Section grant targets must be distinct exact UUIDs') from None
        identity=(op,rule['source_id'],rule['container_id'])
        if identity in seen:raise SafetyError('Duplicate native grant selector; combine its field scope explicitly')
        seen.add(identity)
    return scope


class NativeGrants:
    def __init__(self,engine):self.engine=engine;self.store=engine.store;self.name=engine.name;self.account=engine.account
    @contextmanager
    def locked(self):
        self.store.ready();fd=os.open(self.store.root/'native-grants.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('Invalid native grant lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
        except BlockingIOError:raise SafetyError('Native grant admission is busy; inspect status') from None
        finally:os.close(fd)
    def container(self,source,container):
        status=selected_read_access(self.name)
        if source not in status['source_ids']:raise SafetyError('Native grant source is unselected')
        if container==source:return {'source_id':source,'container_id':source,'role':'account_root'}
        if self.name=='notes':return self.engine.adapter.private_folder(container,source)
        values=[c for c in self.engine.adapter.lists() if c.get('source_id')==source and c.get('id')==container]
        if len(values)!=1 or not values[0].get('writable'):raise SafetyError('Native grant list is absent or not writable')
        return values[0]
    def scope_snapshot(self,scope):
        result=[]
        for rule in scope['rules']:
            root=rule['operation'] in {'create_folder','create_list','create_group','create_smart_list'}
            if not root and rule['container_id']==rule['source_id']:raise SafetyError('An item operation needs a real container ID')
            if rule['operation'] in {'create_list','create_group','create_smart_list'} and rule['container_id']!=rule['source_id']:raise SafetyError('List creation targets its selected source root')
            if self.name=='reminders' and rule['operation']=='create_smart_list':
                from .native_reminder_groups import inventory
                from .native_reminder_moves import list_scope
                inventory(self.engine.adapter,rule['source_id'])
                result.append({'source':self.container(rule['source_id'],rule['container_id']),
                               'destinations':[list_scope(self.engine.adapter,rule['source_id'],target) for target in rule['destinations']]});continue
            if self.name=='reminders' and rule['operation']=='add_image_attachment':
                from .native_reminder_moves import list_scope
                result.append({'source':list_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation']=='add_file_attachment':
                from .native_reminder_moves import list_scope
                result.append({'source':list_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation']=='rename_grocery_category':
                from .native_grocery_categories import private_scope
                source=private_scope(self.engine.adapter,rule['source_id'],rule['container_id']);inventory=self.engine.adapter.reader.read('list_sections',list_id=rule['container_id']);known={v['id']for v in inventory['sections']}
                if any(v not in known for v in rule['destinations']):raise SafetyError('Select exact existing categories of this same grocery list')
                result.append({'source':source,'destinations':[{'id':v,'source_id':rule['source_id'],'list_id':rule['container_id'],'role':'private_grocery_category'}for v in rule['destinations']]});continue
            if self.name=='reminders' and rule['operation']=='create_grocery_category':
                from .native_grocery_categories import private_scope
                result.append({'source':private_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation']=='set_grocery_categorization':
                from .native_grocery_mode import snapshot
                value=snapshot(self.engine.adapter,{'source_id':rule['source_id'],'list_id':rule['container_id']})
                result.append({'source':value['list'],'destinations':[]});continue
            if self.name=='reminders' and rule['operation']=='create_template':
                from .native_reminder_moves import list_scope
                result.append({'source':list_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation']=='rename_template':
                from .native_template_rename import snapshot
                value=snapshot(self.engine.adapter,{'source_id':rule['source_id'],'id':rule['container_id']})
                result.append({'source':{'id':value['metadata']['id'],'source_id':rule['source_id'],'role':'saved_template'},'destinations':[]});continue
            if self.name=='reminders' and rule['operation'] in {'rename_smart_list','set_smart_list_sources'}:
                from .native_reminder_smart_lists import snapshot
                value=snapshot(rule['source_id'],rule['container_id'],reader=self.engine.adapter.reader)
                destinations=[]
                if rule['operation']=='set_smart_list_sources':
                    from .native_reminder_smart_sources import source_ids
                    from .native_reminder_moves import list_scope
                    source_ids(value);destinations=[list_scope(self.engine.adapter,rule['source_id'],target) for target in rule['destinations']]
                result.append({'source':{'id':value['id'],'source_id':value['source_id'],'role':'custom_smart_list'},'destinations':destinations});continue
            if self.name=='reminders' and rule['operation']=='set_grocery_category':
                from .native_grocery_categories import private_scope as list_scope
                from .native_grocery_assignment import sections
                from .native_reminder_section_store import read
                source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id']);values=sections(self.engine.adapter,rule['source_id'],rule['container_id'])
                read(rule['source_id'],rule['container_id']);known={v['id'] for v in values['sections']};destinations=[]
                for target in rule['destinations']:
                    if target!=rule['container_id'] and target not in known:raise SafetyError('Section grant destinations must be exact same-list sections or this list ID for removal')
                    destinations.append({'id':target,'source_id':rule['source_id'],'list_id':rule['container_id'],'role':'private_list_root' if target==rule['container_id'] else 'private_list_section'})
                result.append({'source':source,'destinations':destinations});continue
            if self.name=='reminders' and rule['operation']=='set_section':
                from .native_reminder_moves import list_scope
                from .native_reminder_sections import sections
                from .native_reminder_section_store import read
                source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id']);values=sections(self.engine.adapter,rule['source_id'],rule['container_id'])
                read(rule['source_id'],rule['container_id']);known={v['id'] for v in values['sections']};destinations=[]
                for target in rule['destinations']:
                    if target!=rule['container_id'] and target not in known:raise SafetyError('Section grant destinations must be exact same-list sections or this list ID for removal')
                    destinations.append({'id':target,'source_id':rule['source_id'],'list_id':rule['container_id'],'role':'private_list_root' if target==rule['container_id'] else 'private_list_section'})
                result.append({'source':source,'destinations':destinations});continue
            if self.name=='reminders' and rule['operation']=='reparent_reminder':
                from .native_reminder_moves import list_scope
                source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id']);destinations=[]
                for target in rule['destinations']:
                    if target==rule['container_id']:destinations.append({'id':target,'source_id':rule['source_id'],'role':'private_list_root'});continue
                    observed=self.engine.adapter.snapshot({'operation':'update_reminder','source_id':rule['source_id'],'id':target,'fields':{}});public=observed.get('item',{});native=observed.get('native_metadata',{})
                    if (public.get('id')!=target or public.get('source_id')!=rule['source_id'] or public.get('list_id')!=rule['container_id'] or public.get('public_metadata_complete') is not True or public.get('completed') is not False or public.get('has_recurrence') is not False
                            or native.get('is_subtask') is not False or native.get('parent_id') is not None or native.get('section') is not None or native.get('section_status')!='provider_not_reported'):raise SafetyError('Reparent grant destinations are exact incomplete nonrecurring roots in this private list, or this list ID for promotion')
                    destinations.append({'id':target,'source_id':rule['source_id'],'list_id':rule['container_id'],'role':'private_root_parent'})
                result.append({'source':source,'destinations':destinations});continue
            if self.name=='reminders' and rule['operation'] in {'group_list','ungroup_list'}:
                from .native_reminder_groups import inventory
                from .native_list_groups import root_anchor
                from .native_reminder_moves import list_scope
                values=inventory(self.engine.adapter,rule['source_id']);source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id']);destinations=[]
                for target in rule['destinations']:
                    if rule['operation']=='group_list':
                        if not any(g['id']==target for g in values['groups']):raise SafetyError('Grouping grant requires exact private root group destinations')
                        destinations.append({'id':target,'source_id':rule['source_id'],'role':'private_root_group'})
                    else:destinations.append(root_anchor(self.engine.adapter,rule['source_id'],target,values))
                result.append({'source':source,'destinations':destinations});continue
            if self.name=='reminders' and rule['operation'] in {'rename_group','reorder_group'}:
                from .native_reminder_groups import inventory
                values=inventory(self.engine.adapter,rule['source_id'])
                if not any(g['id']==rule['container_id'] for g in values['groups']):raise SafetyError('Grant needs an exact private root group')
                destinations=[]
                if rule['operation']=='reorder_group':
                    from .native_list_groups import root_anchor
                    destinations=[root_anchor(self.engine.adapter,rule['source_id'],target,values) for target in rule['destinations']]
                result.append({'source':{'source_id':rule['source_id'],'id':rule['container_id'],'role':'private_root_group'},'destinations':destinations});continue
            if self.name=='notes' and rule['operation']=='delete_note':
                from .native_notes_delete import endpoints
                from .native_notes_restore import stable_roles
                source,target,roles=endpoints(self.engine.adapter,rule['source_id'],rule['container_id'],require_visible=False)
                result.append({'source':dict(source,verified_trash_roles=stable_roles(roles)),'destinations':[target]});continue
            if self.name=='notes' and rule['operation']=='restore_note':
                from .native_notes_restore import endpoints,stable_roles
                destinations=[];roles={}
                for destination in rule['destinations']:
                    _,target,checked=endpoints(self.engine.adapter,rule['source_id'],rule['container_id'],destination,resolve_origin=False)
                    destinations.append(target)
                    for role in stable_roles(checked):
                        if role['folder_id'] in roles and roles[role['folder_id']]!=role:raise SafetyError('Recovery folder roles changed during grant review')
                        roles[role['folder_id']]=role
                result.append({'source':{'source_id':rule['source_id'],'container_id':rule['container_id'],'verified_recovery_roles':[roles[key] for key in sorted(roles)]},'destinations':destinations});continue
            if self.name=='notes' and rule['operation']=='move_note':
                destinations=[];roles={};source=None
                for destination in rule['destinations']:
                    origin,target,checked=self.engine.adapter.move_folders({'source_id':rule['source_id'],
                        'folder_id':rule['container_id'],'fields':{'destination_folder_id':destination}})
                    if source is not None and source!=origin:raise SafetyError('Move source changed during grant review')
                    source=origin;destinations.append(target)
                    for role in checked:
                        # Membership writes can increment the native revision.
                        # Roles, names, account relations and ancestry stay bound.
                        stable={key:value for key,value in role.items() if key!='revision'}
                        if role['folder_id'] in roles and roles[role['folder_id']]!=stable:raise SafetyError('Move folder role changed during grant review')
                        roles[role['folder_id']]=stable
                result.append({'source':dict(source,verified_move_roles=[roles[key] for key in sorted(roles)]),'destinations':destinations})
                continue
            if self.name=='reminders' and rule['operation']=='delete_reminder':
                from .native_reminder_moves import list_scope
                result.append({'source':list_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation'] in {'set_list_badge','set_list_pinned'}:
                from .native_reminder_moves import list_scope
                result.append({'source':list_scope(self.engine.adapter,rule['source_id'],rule['container_id']),'destinations':[]});continue
            if self.name=='reminders' and rule['operation'] in {'move_reminder','restore_reminder'}:
                from .native_reminder_moves import list_scope
                source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id'])
                destinations=[list_scope(self.engine.adapter,rule['source_id'],value) for value in rule['destinations']]
                result.append({'source':source,'destinations':destinations});continue
            source=self.container(rule['source_id'],rule['container_id'])
            if (self.name=='notes' and rule['operation'] in {'copy_observation','create_note','append_note','prepend_note','replace_text','update_note','create_folder'}
                    and rule['container_id']!=rule['source_id']):
                source=self.engine.adapter.checked_content_folder_role(source,rule['source_id'])
                if 'verified_store_roles' in source:
                    # Our own content writes can advance folder revisions. Bind
                    # every role, title and parent field; batches use fresh full
                    # revisions in their separately checked before snapshots.
                    source=dict(source,verified_store_roles=[{key:value for key,value in role.items() if key!='revision'} for role in source['verified_store_roles']])
            if rule['operation'] in {'rename_folder','rename_list'}:source={k:v for k,v in source.items() if k!='title'}
            if rule['operation']=='set_list_color':source={k:v for k,v in source.items() if k!='color'}
            if rule['operation']=='rename_folder':
                role=self.engine.adapter.reader.read('folder_role',folder_id=rule['container_id'])
                inventory=self.engine.adapter.reader.read('folder_inventory',folder_id=rule['container_id'])
                root=[f for f in inventory.get('folders',[]) if f.get('id')==rule['container_id']]
                if (role.get('storage_kind')!='regular' or role.get('source_id')!=rule['source_id'] or role.get('folder_id')!=rule['container_id']
                        or len(root)!=1 or root[0].get('is_default') is not False):raise SafetyError('Rename grant requires a verified ordinary nondefault folder')
                source['storage_role']={k:role[k] for k in ('source_id','folder_id','storage_kind','marked_for_deletion','has_smart_query','schema_sha256')}
            if rule['operation']=='reorder_reminder':
                from .native_reminder_moves import list_scope
                source=list_scope(self.engine.adapter,rule['source_id'],rule['container_id']);destinations=[]
                graph=self.engine.adapter.reader.read('list_order',list_id=rule['container_id'])
                for anchor in rule['destinations']:
                    from .native_reminder_moves import subtree
                    subtree(graph,anchor);destinations.append({'id':anchor,'source_id':rule['source_id'],'list_id':rule['container_id']})
            elif rule['operation']=='create_subtask':
                destinations=[]
                for parent_id in rule['destinations']:
                    parent=self.engine.adapter.reader.read('get',id=parent_id)['item']
                    if parent.get('id')!=parent_id or parent.get('source_id')!=rule['source_id'] or parent.get('list_id')!=rule['container_id']:raise SafetyError('Subtask grant parent is absent or outside its exact source and list')
                    destinations.append({'id':parent_id,'source_id':rule['source_id'],'list_id':rule['container_id']})
            elif rule['operation']=='rename_section':
                sections=self.engine.adapter.reader.read('list_sections',list_id=rule['container_id'])
                if sections.get('source_id')!=rule['source_id'] or sections.get('list_id')!=rule['container_id']:raise SafetyError('Section grant list scope is invalid')
                destinations=[]
                for section_id in rule['destinations']:
                    matches=[s for s in sections.get('sections',[]) if isinstance(s.get('id'),str) and s['id'].upper()==section_id.upper()]
                    if len(matches)!=1:raise SafetyError('Section grant target is absent or outside the exact list')
                    destinations.append({'id':matches[0]['id'],'source_id':rule['source_id'],'list_id':rule['container_id']})
            else:destinations=[self.container(rule['source_id'],d) for d in rule['destinations']]
            result.append({'source':source,'destinations':destinations})
        return result
    def prepare(self,scope,*,_replaces=None):
        validate_scope(self.name,scope,self.store.clock())
        context=self.engine.context(True)
        payload={'account':self.account,'context':context,'scope':scope,
             'containers':self.scope_snapshot(scope),'effects':{
                 'authority':'Ongoing exact rule scope; no per-batch receipt within the active grant. Each batch is immutable and consumed once.',
                 'lifetime':'Until revoked' if scope['expires'] is None else 'Until the selected expiry or revocation',
                 'configuration':'No service, source, OS permission or write-mode changes.',
                 'exclusions':'No permanent deletion, collaboration changes, invitations, locks, recording or external export.',
                 'sharing':'Notes private ancestors checked; EventKit does not expose reminder-list participant state.',
                 'quotas':'Every affected native object counts, including moved descendants and uncertain or refused items; no automatic refund.'}}
        if self.name=='notes':payload['effects']['folder_role']=('The optional native model checks selected content folders and their ancestors before admission; separate provider reads are not atomic.' if native_config('notes').get('folder_write_backend')=='notes-shared' else 'Notes content authority is limited to the account default creation folder; its identity is rechecked before dispatch.')
        if self.name=='notes' and native_config('notes').get('folder_write_backend')!='notes-shared' and native_config('notes').get('metadata_backend')=='notes-store-readonly':
            payload['effects']['folder_role']='Explicit read-only metadata verifies selected ordinary folders and private ancestors. Roles, account, titles and parents remain grant-bound; each batch rechecks full current revisions. Smart, shared and marked-deleted targets are excluded; only explicit recovery operations admit the verified system recovery folder.'
        if self.name=='notes' and any(rule['operation'] in {'delete_note','restore_note'} for rule in scope['rules']):
            payload['effects']['recovery_scope']='Only explicitly selected delete_note or restore_note rules admit the uniquely verified selected-account system Recently Deleted folder. Deletion moves the existing object there and refuses empty or inaccessible recovery destinations; restoration requires an exact approved ordinary destination. No permanent deletion, empty Trash or placeholder creation.'
        if self.name=='notes' and any(rule['operation']=='move_note' for rule in scope['rules']):
            payload['effects']['move_scope']='Moves require explicitly selected read-only metadata, ordinary unshared source/destination ancestors and exact destination IDs. Roles and ancestry are rechecked at admission; every note retains its own content and attachment snapshot. No Recently Deleted, cross-account or shared moves.'
        if self.name=='reminders' and any(rule['operation']=='move_reminder' for rule in scope['rules']):
            payload['effects']['move_scope']='Move a root together with all reviewed descendants within the selected source and exact private physical lists. Every descendant counts against batch and rolling object caps. Section migration, recurring reminders and subtask detachment are refused; display ordering and unexposed metadata remain unverified.'
        if self.name=='reminders' and any(rule['operation'] in {'delete_reminder','restore_reminder'} for rule in scope['rules']):
            payload['effects']['recovery_scope']='Only originally reviewed private nonrecurring root leaves without descendants or sections. Deletion is recoverable and never purges; restoration requires the completed owned deletion ledger and exact approved destination, preserves the native ID and can reactivate original alarms and due notifications. Unexposed recovery content remains unverified until restore readback. Delete and restore require separate explicit rules.'
            if any(rule['operation'] in {'delete_reminder','restore_reminder'} and 'include_descendants' in rule['fields'] for rule in scope['rules']):
                payload['effects']['recovery_scope']='Default lifecycle operations remain root-leaf-only. A rule explicitly admitting include_descendants can review one private nonrecurring root with up to nine direct children; every family member counts against batch and rolling quotas. Nested, shared, grocery and sectioned families refuse. Deletion never purges. Restoration requires the completed owned deletion ledger, explicit descendant inclusion and original list, and may change manual positions or reactivate alarms. Unexposed content is verified only after restore readback. Delete and restore require separate explicit rules.'
        if self.name=='reminders' and any(rule['operation']=='group_list' and 'allow_nonempty' in rule['fields'] for rule in scope['rules']):
            payload['effects']['populated_group_scope']='Explicit allow_nonempty authority permits the selected root physical list to join a private root group with fewer than100 observed physical/custom Smart List members. Every batch binds and preserves existing members; no nested group, Smart List move or task edit is authorized. A rule lacking this field cannot authorize a populated destination.'
        if self.name=='reminders' and any(rule['operation']=='reparent_reminder' for rule in scope['rules']):
            payload['effects']['parent_scope']='Move one existing incomplete nonrecurring leaf within this private ordinary list. Exact destination IDs select root parents, or the list ID for root promotion. Parent, sibling anchor and explicit placement are bound in each plan. The leaf and both affected parents count against quotas; no descendants, sections, completion or content changes.'
        if self.name=='reminders' and any(rule['operation']=='reorder_reminder' for rule in scope['rules']):
            payload['effects']['ordering_scope']='Only exact selected private ordinary lists and approved root anchor IDs. Every descendant of the reordered root counts against quotas; no reparenting or section migration. Native manual sequence is verified; app display sorting can differ.'
        if any(rule['operation']=='copy_observation' for rule in scope['rules']):
            payload['effects']['observation_copy']='Allows new lossy copies from caller-supplied observations in this exact scope; originals are unchanged, and copies do not retain original identity, native metadata or attachments. Duplicates may result. Each batch discloses its omissions and selected fields.'
            if self.name=='reminders':payload['effects']['observation_copy']+=' Reminder copies start incomplete; copied dates and absolute alarms can affect notifications controlled by Apple. Past alarms may notify immediately.'
        if _replaces is not None:
            payload['replaces']={'grant_id':_replaces['plan_id'],'sha256':digest(_replaces)}
            payload['previous_scope']=_replaces['payload']['scope']
            payload['effects']['replacement']='Retire the exact prior grant before activating this scope; rolling quota history carries forward.'
        return self.store.prepare(self.name+'_grant',payload)
    def prepare_replacement(self,grant_id,scope):
        return self.prepare(scope,_replaces=self.checked(grant_id))
    def lineage(self,grant):
        chain={};current=grant
        for _ in range(64):
            identity=current['plan_id']
            if identity in chain:raise SafetyError('Cyclic native grant replacement history')
            chain[identity]=current;parent=current['payload'].get('replaces')
            if parent is None:return chain
            if not isinstance(parent,dict) or set(parent)!={'grant_id','sha256'}:raise SafetyError('Invalid native replacement binding')
            previous=self.owned(parent['grant_id'])
            if parent['sha256']!=digest(previous):raise SafetyError('Native replacement history changed')
            current=previous
        raise SafetyError('Native grant replacement history exceeds the verified bound')
    def retirement(self,plan):
        parent=plan['payload'].get('replaces')
        if parent is None:return None
        previous=self.owned(parent['grant_id'])
        if (parent!={'grant_id':previous['plan_id'],'sha256':digest(previous)}
                or plan['payload'].get('previous_scope')!=previous['payload']['scope']):raise SafetyError('Native grant replacement binding or before-scope changed')
        record={'grant_sha256':digest(previous),'replaced_by':plan['plan_id'],'replacement_sha256':digest(plan)}
        return self.store.path(previous['plan_id'],'.native-grant-revoked'),record
    def retire_previous(self,plan):
        retirement=self.retirement(plan)
        if retirement is None:return
        path,record=retirement
        if path.exists():
            if self.store.read(path)!=record:raise SafetyError('Prior native grant was revoked outside this replacement; review a fresh grant')
        else:self.store.write_once(path,record)

    def owned(self,grant_id):
        plan=self.store.read(self.store.path(grant_id,'.json'))
        if plan.get('kind')!=self.name+'_grant' or plan.get('payload',{}).get('account')!=self.account:raise SafetyError('Native grant belongs to another service or owner')
        return plan
    def checked(self,grant_id,*,active=True,containers=True):
        plan=self.owned(grant_id);payload=plan['payload'];validate_scope(self.name,payload['scope'],self.store.clock())
        if self.store.path(grant_id,'.native-grant-revoked').exists():raise SafetyError('Native grant revoked')
        if payload['context']!=self.engine.context(True):raise SafetyError('Native grant access, backend or policy changed')
        if containers and payload['containers']!=self.scope_snapshot(payload['scope']):raise SafetyError('Native grant container metadata changed; review a replacement')
        if active:
            if not self.store.path(grant_id,'.result').exists() or not self.store.path(grant_id,'.started').exists():raise SafetyError('Native grant is not active')
            result=self.store.read(self.store.path(grant_id,'.result'));started=self.store.read(self.store.path(grant_id,'.started'))
            if result!={'status':'completed','grant_sha256':digest(plan),'grant_active':True} or started.get('sha256')!=digest(plan):raise SafetyError('Native grant activation is incomplete or mismatched')
            self.admitted_activation(plan)
            retired=self.retirement(plan)
            if retired is not None and (not retired[0].exists() or self.store.read(retired[0])!=retired[1]):raise SafetyError('Prior native grant retirement is not proven')
        if self.store.path(grant_id,'.native-grant-revoked').exists() or payload['context']!=self.engine.context(True):
            raise SafetyError('Native grant authority changed during container verification')
        return plan
    def admitted_activation(self,plan):
        identity=plan['plan_id'];started=self.store.read(self.store.path(identity,'.started'))
        policy=plan['payload']['context']['approval_policy']
        authority='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt'
        if started.get('sha256')!=digest(plan) or started.get('account')!=self.account or started.get('authorization')!=authority:
            raise SafetyError('Native grant activation admission proof is invalid')
        if policy=='local-receipt':
            receipt=self.store.read(self.store.path(identity,'.approved'))
            if receipt.get('plan_id')!=identity or receipt.get('sha256')!=digest(plan):raise SafetyError('Native grant receipt does not match')
    def finalize(self,grant_id):
        """Complete an interrupted admitted activation; no native write or new approval."""
        selected_read_access(self.name)
        with self.engine.serialized(),self.locked():
            plan=self.checked(grant_id,active=False);self.admitted_activation(plan)
            self.retire_previous(plan)
            result={'status':'completed','grant_sha256':digest(plan),'grant_active':True}
            path=self.store.path(grant_id,'.result')
            if path.exists():
                if self.store.read(path)!=result:raise SafetyError('Native grant result is inconsistent')
                return result
            return self.store.finish(grant_id,result)
    def activate(self,grant_id):
        # Serialize ordinary activation with native mutations; admission still uses
        # the existing strict receipt/trusted-client policy, never a minted receipt.
        selected_read_access(self.name)
        with self.engine.serialized(),self.locked():
            self.engine.assert_settled();plan=self.checked(grant_id,active=False)
            parent=plan['payload'].get('replaces')
            if parent is not None:
                previous=self.checked(parent['grant_id'])
                if parent!={'grant_id':previous['plan_id'],'sha256':digest(previous)}:raise SafetyError('Prior native grant changed before replacement approval')
                self.lineage(plan)
            admitted=self.store.consume(grant_id,self.account)
            if admitted!=plan:raise SafetyError('Native grant changed at activation')
            self.retire_previous(plan)
            return self.store.finish(grant_id,{'status':'completed','grant_sha256':digest(plan),'grant_active':True})
    def revoke(self,grant_id):
        from .native_access import access_status
        access_status(self.name)
        # This short lock can run between a batch's items; it never waits for an
        # in-flight native operation to finish or claims to recall its effect.
        with self.locked():
            plan=self.owned(grant_id);path=self.store.path(grant_id,'.native-grant-revoked')
            if not path.exists():self.store.write_once(path,{'grant_sha256':digest(plan),'revoked_at':self.store.clock()})
        return {'status':'revoked','grant_id':grant_id,'in_flight_effects_recalled':False}
    def status(self,grant_id):
        from .native_access import access_status
        access_status(self.name);plan=self.owned(grant_id)
        if self.store.path(grant_id,'.native-grant-revoked').exists():state='revoked'
        elif not self.store.path(grant_id,'.started').exists():state='pending_activation'
        else:
            try:self.checked(grant_id,containers=False);state='active'
            except (SafetyError,OSError):state='inactive_or_unverified'
        try:self.engine.assert_settled();admission='requires_current_scope_and_native_checks'
        except (SafetyError,OSError):admission='blocked_by_unfinished_native_operation'
        return {'grant_id':grant_id,'status':state,'write_admission':admission,'sha256':digest(plan),'scope':plan['payload']['scope'],
                'container_state':'not_refreshed','configuration_changed':False,'replaces':plan['payload'].get('replaces')}
    def match(self,grant,item):
        scope=grant['payload']['scope'];op=item['operation']
        if op=='copy_observation':raise SafetyError('Observation copies must use the reviewed copy preparation path')
        marker=item.get('standing_operation');scope_op=op if marker is None else marker
        if marker is not None and (marker!='copy_observation' or op!=('create_note' if self.name=='notes' else 'create_reminder')):
            raise SafetyError('Invalid observation copy grant operation')
        container=(item['list_id'] if op=='create_template' else item['id'] if op in {'rename_template','rename_group','reorder_group','rename_smart_list','set_smart_list_sources'} else item.get('before',{}).get('original',{}).get('item',{}).get('list_id') if op=='restore_reminder' else item.get('parent_folder_id',item['source_id']) if op=='create_folder' else item['source_id'] if op in {'create_list','create_group','create_smart_list'}
                   else item.get('folder_id') if self.name=='notes' else item.get('list_id') if op in {'add_image_attachment','add_file_attachment','rename_grocery_category','create_grocery_category','set_grocery_categorization','create_reminder','create_section','rename_section','rename_list','set_list_color','set_list_badge','set_list_pinned','group_list','ungroup_list','set_grocery_category','set_section','reparent_reminder'}
                   else item.get('before',{}).get('item',{}).get('list_id'))
        fields=item.get('requested_fields')
        if not isinstance(fields,list) or not fields:raise SafetyError('Native standing plan lacks exact requested field scope')
        requires_location=False
        if self.name=='reminders' and op in {'create_reminder','update_reminder'} and 'alarms' in fields:
            requested=item.get('fields',{}).get('alarms',[])
            prior=item.get('before',{}).get('item',{}).get('alarms',[])
            if not isinstance(requested,list) or not isinstance(prior,list):raise SafetyError('Location-alarm scope cannot be verified')
            requires_location=any(isinstance(x,dict) and 'location' in x for x in requested) or any(isinstance(x,dict) and x.get('structured_location') is not None for x in prior)
        for rule in scope['rules']:
            if (scope_op==rule['operation'] and item['source_id']==rule['source_id'] and container==rule['container_id']
                    and set(fields)<=set(rule['fields'])):
                if requires_location and 'location_alarms' not in rule['fields']:continue
                dest=item['fields'].get('destination_folder_id' if self.name=='notes' else 'list_id')
                if op in {'move_note','restore_note','move_reminder','restore_reminder'} and dest not in rule['destinations']:continue
                if op in {'create_smart_list','set_smart_list_sources'} and not set(item['fields']['list_ids'])<=set(rule['destinations']):continue
                if op=='rename_grocery_category' and item['id']not in rule['destinations']:continue
                if op=='set_grocery_category' and (item['fields']['category_id'] if item['fields']['category_id'] is not None else item['list_id']) not in rule['destinations']:continue
                if op=='set_section' and (item['fields']['section_id'] if item['fields']['section_id'] is not None else item['list_id']) not in rule['destinations']:continue
                if op=='reparent_reminder' and (item['fields']['parent_id'] if item['fields']['parent_id'] is not None else item['list_id']) not in rule['destinations']:continue
                if op=='reorder_group' and item['fields']['anchor_id'] not in rule['destinations']:continue
                if op in {'group_list','ungroup_list'} and item['fields']['group_id' if op=='group_list' else 'anchor_id'] not in rule['destinations']:continue
                if op=='reorder_reminder' and item['fields']['anchor_id'] not in rule['destinations']:continue
                if op=='create_subtask' and item['id'] not in rule['destinations']:continue
                if op=='rename_section' and item['section_id'].upper() not in {value.upper() for value in rule['destinations']}:continue
                return
        raise SafetyError('Native change exceeds the approved scenario source/container/operation/fields')
    def batch_count(self,items,*,legacy=False):
        count=0
        for item in items:
            if self.name=='reminders' and item.get('operation')=='add_image_attachment':
                from .native_reminder_image_create import charge
                count+=charge(item);continue
            if self.name=='reminders' and item.get('operation')=='add_file_attachment':
                from .native_reminder_file_create import charge
                count+=charge(item);continue
            if self.name=='reminders' and item.get('operation')=='create_template':
                from .native_template_create import charge
                count+=charge(item);continue
            if self.name=='reminders' and item.get('operation') in {'delete_reminder','restore_reminder'} and item.get('fields',{}).get('include_descendants') is True:
                from .native_reminder_family_lifecycle import charge
                count+=charge(item);continue
            if self.name=='reminders' and item.get('operation')=='reparent_reminder':
                from .native_reminder_parents import charge
                count+=charge(item);continue
            if self.name=='reminders' and item.get('operation') in {'move_reminder','reorder_reminder'}:
                if 'source_graph' in item.get('before',{}):
                    from .native_reminder_moves import charge
                    count+=charge(item);continue
                if not legacy:raise SafetyError('Move quota requires a complete reviewed subtree')
            count+=1
        return count
    def check_batch(self,plan,grant):
        binding=plan['payload'].get('standing_grant')
        if binding!={'grant_id':grant['plan_id'],'sha256':digest(grant)}:raise SafetyError('Native standing batch does not bind the active grant')
        items=plan['payload']['changes']
        if not 1<=len(items)<=grant['payload']['scope']['max_batch'] or self.batch_count(items)>grant['payload']['scope']['max_batch']:raise SafetyError('Native standing batch exceeds its approved native-object bound')
        copy=plan['payload'].get('copy_review');marked=[item.get('standing_operation')=='copy_observation' for item in items]
        expected={'notes':'create_lossy_note_copy_from_user_supplied_observation','reminders':'create_lossy_copies_from_user_supplied_observation'}[self.name]
        if any(marked) or copy is not None:
            if not all(marked) or not isinstance(copy,dict) or copy.get('operation')!=expected or copy.get('accepted_omissions') is not True:
                raise SafetyError('Observation copy batch lacks its exact reviewed omissions')
        for item in items:self.match(grant,item)
    def consume(self,plan_id):
        # The engine holds the native-execution lock. This lock serializes grant
        # quotas/revocation with admission. The existing ledger blocks uncertainty.
        with self.locked():
            plan=self.store.proposal(plan_id);binding=plan['payload'].get('standing_grant',{})
            grant=self.checked(binding.get('grant_id'));self.check_batch(plan,grant);lineage=self.lineage(grant)
            if plan.get('kind')!=self.name+'_change' or plan['payload'].get('account')!=self.account:raise SafetyError('Wrong native standing owner/service')
            self.engine.assert_settled(plan);now=self.store.clock();scope=grant['payload']['scope'];count=self.batch_count(plan['payload']['changes']);used=0
            for path in self.store.root.glob('*.native-grant-use'):
                entry=self.store.read(path)
                if entry.get('grant_id') not in lineage:continue
                original_grant=lineage[entry['grant_id']]
                if type(entry.get('time')) not in (int,float) or not math.isfinite(entry['time']) or entry['time']>now:raise SafetyError('Native grant audit time is invalid; inspect clock changes')
                if type(entry.get('count')) is not int or entry['count']<1:raise SafetyError('Invalid native grant quota audit')
                admitted=self.store.read(self.store.path(path.name.removesuffix('.native-grant-use'),'.json'))
                if (entry.get('grant_sha256')!=digest(original_grant) or entry.get('plan_sha256')!=digest(admitted)
                        or admitted.get('payload',{}).get('standing_grant')!={'grant_id':original_grant['plan_id'],'sha256':digest(original_grant)}
                        or entry['count']!=self.batch_count(admitted.get('payload',{}).get('changes',[]),legacy=True)):raise SafetyError('Native grant quota audit does not bind its original batch')
                if entry['time']>=now-86400:used+=entry['count']
            if scope['max_24h'] is not None and used+count>scope['max_24h']:raise SafetyError('Native grant rolling 24-hour item cap reached')
            self.store.write_once(self.store.path(plan_id,'.native-grant-use'),{'grant_id':grant['plan_id'],'grant_sha256':digest(grant),'plan_sha256':digest(plan),'count':count,'time':now})
            self.store.write_once(self.store.path(plan_id,'.started'),{'started':now,'sha256':digest(plan),'account':self.account,
                   'authorization':'standing-native-grant','grant_id':grant['plan_id'],'grant_sha256':digest(grant)})
            return plan
