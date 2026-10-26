"""Fixed native mutation envelopes, shared by execution and refusal-proof checks.

Approval plans retain full previews. Helpers receive only fields their fixed
operation uses, avoiding duplicate projections and review-only metadata in IPC.
"""


def mutation_request(service,item):
    if service not in {'notes','reminders'}:raise ValueError('Unknown native service')
    operation=item.get('operation')
    if service=='notes' and operation in {'create_note','create_folder','append_note','prepend_note','replace_text','update_note','repair_note_body','move_note'}:
        change={key:item[key] for key in ('operation','source_id','folder_id','parent_folder_id','id') if key in item}
        keys={'title'} if operation=='create_folder' else {'title','destination_folder_id'} if operation=='move_note' else {'title','html'}
        change['fields']={key:item['fields'][key] for key in keys if key in item['fields']}
        if operation in {'append_note','prepend_note','replace_text','update_note','repair_note_body','move_note'}:
            before=item['before'];change['before']={'note':{key:before['note'][key] for key in ('title','html','modified')}}
            if operation=='move_note':change['before']['attachments']=before['attachments']
    elif service=='reminders' and operation in {'create_list','create_reminder','update_reminder','set_completed','move_reminder','rename_list'}:
        change={key:item[key] for key in ('operation','source_id','list_id','id','fields') if key in item}
        if operation in {'update_reminder','set_completed','move_reminder'}:change['before']={'item':item['before']['item']}
        if operation=='rename_list':change['before']=item['before']
    else:
        # Unknown operations are never introduced by the fixed MCP adapters.
        # Preserve their data for synthetic/negative protocol tests; the helper
        # rejects them before dispatch rather than interpreting an extension.
        change=item
    return {'protocol_version':1,'service':service,'action':'apply','source_ids':[item['source_id']],'change':change}
