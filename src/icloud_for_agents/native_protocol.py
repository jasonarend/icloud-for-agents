"""Fixed native mutation envelopes, shared by execution and refusal-proof checks.

Approval plans retain full previews. Helpers receive only fields their fixed
operation uses, avoiding duplicate projections and review-only metadata in IPC.
"""


def mutation_request(service,item):
    if service not in {'notes','reminders'}:raise ValueError('Unknown native service')
    operation=item.get('operation')
    if service=='notes' and operation in {'create_note','create_folder','append_note','prepend_note','replace_text','update_note','repair_note_body','move_note','rename_folder'}:
        content_roles=None
        change={key:item[key] for key in ('operation','source_id','folder_id','parent_folder_id','id') if key in item}
        if operation in {'create_note','append_note','prepend_note','replace_text','update_note','repair_note_body'} or (operation=='create_folder' and item.get('parent_folder_id')):
            before=item.get('before',{})
            folder=(before.get('parent') if operation=='create_folder' else before.get('folder') if operation in {'append_note','prepend_note','replace_text','update_note','repair_note_body'} else before)
            target=item.get('parent_folder_id') if operation=='create_folder' else item.get('folder_id')
            model=folder.get('verified_model_role') if isinstance(folder,dict) else None
            default=folder.get('verified_default_folder') if isinstance(folder,dict) else None
            stored=folder.get('verified_store_roles') if isinstance(folder,dict) else None
            if isinstance(model,dict) and model.get('folder_id')==target and model.get('source_id')==item['source_id'] and model.get('role')=='ordinary_non_system':
                change['folder_guard']='verified_model'
            elif isinstance(default,dict) and default=={'id':target,'source_id':item['source_id']}:
                change['folder_guard']='default_folder'
            elif isinstance(stored,list) and stored and any(role.get('folder_id')==target and role.get('source_id')==item['source_id'] for role in stored):
                change['folder_guard']='verified_store_roles';content_roles=stored
            else:change['folder_guard']='unverified'
        keys={'title'} if operation in {'create_folder','rename_folder'} else {'title','destination_folder_id'} if operation=='move_note' else {'title','html'}
        change['fields']={key:item['fields'][key] for key in keys if key in item['fields']}
        if operation=='rename_folder':
            change['before']={'inventory':item['before']['inventory'],'role':next(r for r in item['before']['roles'] if r['folder_id']==item['id'])}
            change['writer_backend']=item['writer_backend']
            if item.get('legacy_title_restore') is True:change['legacy_title_restore']=True
        if operation in {'append_note','prepend_note','replace_text','update_note','repair_note_body','move_note'}:
            before=item['before'];change['before']={'note':{key:before['note'][key] for key in ('title','html','modified')}}
            if operation=='move_note':
                change['before']['attachments']=before['attachments']
                roles=before.get('roles')
                if isinstance(roles,list) and roles:
                    change['folder_guard']='verified_store_roles';change['before']['roles']=roles
        if content_roles is not None:change.setdefault('before',{})['roles']=content_roles
    elif service=='reminders' and operation in {'create_list','create_reminder','create_section','rename_section','update_reminder','set_completed','move_reminder','rename_list','set_list_color','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}:
        change={key:item[key] for key in ('operation','source_id','list_id','id','child_id','section_id','fields') if key in item}
        if operation in {'update_reminder','set_completed','move_reminder','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}:
            change['before']={'item':item['before']['item']}
            if 'native_metadata' in item['before']:change['before']['native_metadata']=item['before']['native_metadata']
            if 'urgent' in item['before']:change['before']['urgent']=item['before']['urgent']
        if operation in {'rename_list','set_list_color','create_section','rename_section'}:change['before']=item['before']
        if operation=='move_reminder' and 'source_graph' in item['before']:
            from .native_reminder_moves import structure
            before=item['before'];change['hierarchy_guard']='verified_list_graphs'
            change['before'].update(source_scope=before['source_scope'],destination_scope=before['destination_scope'],source_nodes=structure(before['source_graph']),destination_nodes=structure(before['destination_graph']),descendants=[{key:part[key] for key in ('item','native_metadata','urgent')} for part in before['descendants']])
    else:
        # Unknown operations are never introduced by the fixed MCP adapters.
        # Preserve their data for synthetic/negative protocol tests; the helper
        # rejects them before dispatch rather than interpreting an extension.
        change=item
    return {'protocol_version':1,'service':service,'action':'apply','source_ids':[item['source_id']],'change':change}
