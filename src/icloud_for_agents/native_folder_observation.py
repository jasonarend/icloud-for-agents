"""Read-only compatibility for one retained experimental empty-folder move.

Fresh folder moves remain unsupported. An observed tombstone is a failed-route
effect, not a successful move or proof of recoverability. Never mutates Notes.
"""
from .core import SafetyError,digest
from .native_read import native_config
from .native_note_store import parse_identity,empty_folder_membership


def validate_item(item):
    try:
        source=item['source_id'];identity=item['id'];before=item['before'];fields=item['fields']
        if (item['operation']!='move_folder' or item.get('folder_guard')!='verified_store_roles'
                or item['folder_id']!=identity or set(fields)!={'title','destination_folder_id'}
                or set(before)!={'folder','destination','roles','source_inventory','destination_inventory','roots','default_folder_id'}):raise ValueError()
        store,_=parse_identity(source,'ICAccount');folder_store,pk=parse_identity(identity,'ICFolder')
        target=fields['destination_folder_id'];target_pk=None if target==source else parse_identity(target,'ICFolder')[1]
        if folder_store!=store or target==identity or (target!=source and parse_identity(target,'ICFolder')[0]!=store):raise ValueError()
        origin=before['folder'];parent=origin['parent_id'];parent_pk=None if parent==source else parse_identity(parent,'ICFolder')[1]
        if (origin['id']!=identity or origin['source_id']!=source or origin['title']!=fields['title'] or origin['shared'] is not False
                or not isinstance(fields['title'],str) or not 1<=len(fields['title'])<=1000 or parent==target
                or identity==before['default_folder_id']):raise ValueError()
        inventory=before['source_inventory'];rows=inventory['folders']
        if (set(inventory)!={'folders','notes','siblings'} or inventory['notes']!=[] or len(rows)!=1
                or rows[0]!={k:origin[k] for k in ('id','title','source_id','parent_id','shared')}|{'is_default':False}):raise ValueError()
        if target==source:
            if before['destination'] is not None or before['destination_inventory'] is not None:raise ValueError()
        else:
            dest=before['destination'];inv=before['destination_inventory']
            if (dest['id']!=target or dest['source_id']!=source or dest['shared'] is not False or target==before['default_folder_id']
                    or set(inv)!={'folders','notes','siblings'} or inv['notes']!=[] or inv['folders']!=[{k:dest[k] for k in ('id','title','source_id','parent_id','shared')}|{'is_default':False}]):raise ValueError()
        roles=before['roles']
        if not isinstance(roles,list) or not 1<=len(roles)<=64 or len({v['folder_id'] for v in roles})!=len(roles):raise ValueError()
        nodes={}
        for root in [origin,*([before['destination']] if target!=source else [])]:
            ancestors=root.get('private_ancestors',[])
            if not isinstance(ancestors,list) or len(ancestors)>31:raise ValueError()
            chain=[root,*ancestors];seen=set()
            for index,node in enumerate(chain):
                key=node['id']
                if (key in seen or node['source_id']!=source or node['shared'] is not False or key==before['default_folder_id']
                        or parse_identity(key,'ICFolder')[0]!=store
                        or node['parent_id']!=(chain[index+1]['id'] if index+1<len(chain) else source)):raise ValueError()
                seen.add(key)
                if key in nodes and nodes[key]!=node:raise ValueError()
                nodes[key]=node
        if set(nodes)!={v['folder_id'] for v in roles}:raise ValueError()
        for role in roles:
            node=nodes[role['folder_id']];node_parent=None if node['parent_id']==source else parse_identity(node['parent_id'],'ICFolder')[1]
            if (parse_identity(role['folder_id'],'ICFolder')[0]!=store or role['source_id']!=source or role['storage_kind']!='regular'
                    or role['native_folder_type']!=0 or role['marked_for_deletion'] is not False or role['has_smart_query'] is not False
                    or type(role['revision']) is not int or role['parent_pk']!=node_parent or role['title']!=node['title']):raise ValueError()
            if role['folder_id']==identity and (role['parent_pk']!=parent_pk or role['title']!=origin['title']):raise ValueError()
        roots=before['roots']
        if not isinstance(roots,list) or len(roots)>1000 or len({v['id'] for v in roots})!=len(roots):raise ValueError()
        for row in roots:
            if set(row)!={'id','title','source_id','parent_id'} or row['source_id']!=source or row['parent_id']!=source or parse_identity(row['id'],'ICFolder')[0]!=store:raise ValueError()
        return parent_pk,target_pk
    except (KeyError,TypeError,ValueError,AttributeError):
        raise SafetyError('Only a complete retained empty ordinary-folder move can be observed') from None


def observation_matches(item,observed):
    """Bind the stored acceptance proof without performing a new native read."""
    try:
        parent,target=validate_item(item);before=item['before'];old={v['folder_id']:v for v in before['roles']}
        if set(observed)!={'folder_id','source_id','location','before_sha256','roles','empty_membership','coverage'}:return False
        if observed['folder_id']!=item['id'] or observed['source_id']!=item['source_id'] or observed['before_sha256']!=digest(before) or observed['coverage']!=COVERAGE:return False
        roles=observed['roles']
        if not isinstance(roles,list) or len(roles)!=len(old) or {v['folder_id'] for v in roles}!=set(old):return False
        selected=None
        for role in roles:
            prior=old[role['folder_id']]
            if role['folder_id']!=item['id']:
                expected=dict(prior)
                if role['folder_id']==item['fields']['destination_folder_id']:
                    if type(role['revision']) is not int or role['revision']<prior['revision']:return False
                    expected['revision']=role['revision']
                if role!=expected:return False
                continue
            selected=role
            if (type(role['revision']) is not int or role['revision']<prior['revision'] or role['parent_pk'] not in {parent,target}
                    or type(role['marked_for_deletion']) is not bool):return False
            expected=dict(prior,revision=role['revision'],parent_pk=role['parent_pk'],marked_for_deletion=role['marked_for_deletion'],storage_kind='special_smart_or_deleted' if role['marked_for_deletion'] else 'regular')
            if role!=expected:return False
        location='marked_deleted' if selected['marked_for_deletion'] else 'source' if selected['parent_pk']==parent else 'destination'
        return observed['location']==location and observed['empty_membership']=={'folder_id':item['id'],'source_id':item['source_id'],'live_notes':0,'live_child_folders':0,'content_read':False,'database_writes':False}
    except (KeyError,TypeError,ValueError,SafetyError):return False


COVERAGE='Exact native folder roles and absence of live direct children only. Original failure retained; no successful move, restoration, global absence, causal history, cloud synchronization or delayed-effect guarantee. No replay.'


def snapshot(adapter,item):
    validate_item(item)
    if native_config('notes').get('metadata_backend')!='notes-store-readonly':raise SafetyError('Folder observation requires explicitly selected read-only Notes metadata')
    source=item['source_id'];identity=item['id']
    default=adapter.reader.read('default_folder',source_id=source)['folder']
    if default['id']!=item['before']['default_folder_id'] or default['source_id']!=source:raise SafetyError('Selected default folder changed during observation')
    roles=[adapter.reader.read('folder_role',folder_id=v['folder_id']) for v in item['before']['roles']]
    selected=next(v for v in roles if v['folder_id']==identity);parent,_=validate_item(item)
    observed={'folder_id':identity,'source_id':source,'location':'marked_deleted' if selected['marked_for_deletion'] else 'source' if selected['parent_pk']==parent else 'destination',
              'before_sha256':digest(item['before']),'roles':roles,'empty_membership':empty_folder_membership(identity,[source]),'coverage':COVERAGE}
    if not observation_matches(item,observed):raise SafetyError('Folder changed outside the retained move scope; no recovery association')
    if [adapter.reader.read('folder_role',folder_id=v['folder_id']) for v in item['before']['roles']]!=roles:raise SafetyError('Folder metadata changed during observation')
    return observed
