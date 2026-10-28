"""Lazy fixed-protocol native reads. No mutation or permission-request actions."""
import json
import os
from pathlib import Path
import platform
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import subprocess
import tempfile
from typing import Literal

from .core import SafetyError, NativeOperationRefused, digest
from .services import enabled


READ_ACTIONS = {'notes': {'status', 'sources', 'folders', 'default_folder', 'folder_get', 'folder_inventory', 'folder_role', 'model_folder_role', 'query', 'get', 'search', 'attachments', 'diagnose_note', 'contains'},
                'reminders': {'status', 'sources', 'lists', 'default_list', 'list_sections', 'query', 'get','list_membership','recovery_lookup','recovery_tree','recovery_list'}}
MAX_OUTPUT = 2_000_000


def _confirmed_refusal(name,request,response):
    allowed={'permission_required','not_found_in_scope','invalid_fields','invalid_mutation','invalid_request',
        'unsupported_operation','unsupported_service','unsupported_hierarchy_move','unverified_folder_role_move','notes_validation_timeout','native_dispatch_cancelled','duplicate_folder','duplicate_list','shared_write_not_supported',
        'unsupported_repair_target','unsupported_edit_target','snapshot_changed',
        'snapshot_changed_or_recurrence_unsupported','notes_bridge_unavailable','notes_not_running','notes_model_store_unavailable','notes_model_unavailable','notes_model_has_unrelated_changes'}
    category=response.get('category',response.get('error'))
    reason=('permission_required' if type(response.get('error_code')) is int and response['error_code'] in {-1743,-1744} else
            category if isinstance(category,str) and category in allowed else 'helper_validation')
    guidance=' Check Notes app-data access for the signed helper in System Settings > Privacy & Security > Files & Folders. macOS 27 does not show an automatic access prompt. Notes Automation access is separate.' if name=='notes' and reason=='notes_model_store_unavailable' else ''
    return NativeOperationRefused('Native '+name+' refused before mutation dispatch ('+reason+'); the consumed plan cannot be replayed.'+guidance,
        evidence={'mutation_dispatched':False,'request_sha256':digest(request),'helper_response_sha256':digest(response),'reason':reason})


def native_config(name):
    if name not in READ_ACTIONS or not enabled(name):
        raise SafetyError('Native service is disabled or unknown')
    try:
        config = json.loads(os.environ.get('ICLOUD_NATIVE_CONFIG', '{}'))
    except (ValueError, TypeError):
        raise SafetyError('Invalid native configuration') from None
    try:
        validate_config(config)
    except ValueError:
        raise SafetyError("Invalid native configuration") from None
    return config.get(name, {})


def validate_config(config):
    if not isinstance(config, dict) or set(config) - set(READ_ACTIONS):
        raise ValueError('Unknown native service configuration')
    for name, value in config.items():
        if not isinstance(value, dict) or set(value) - {'source_ids', 'helper_path', 'access_mode','metadata_backend','folder_write_backend','advanced_backend'}:
            raise ValueError('Unknown native configuration fields')
        if 'metadata_backend' in value and (name!='notes' or not isinstance(value['metadata_backend'],str) or value['metadata_backend'] not in {'scripting','notes-store-readonly'}):
            raise ValueError('Unknown optional native metadata backend')
        if 'advanced_backend' in value and (name!='reminders' or not isinstance(value['advanced_backend'],str) or value['advanced_backend'] not in {'disabled','reminderkit'}):raise ValueError('Unknown optional native advanced backend')
        if 'folder_write_backend' in value and (name!='notes' or value['folder_write_backend'] not in ('scripting','notes-shared') or (value['folder_write_backend']=='notes-shared' and value.get('metadata_backend')!='notes-store-readonly')):
            raise ValueError('Native folder writer requires explicit read-only store verification')
        if value.get('access_mode', 'read-only') not in {'disabled', 'read-only', 'write-enabled'}:
            raise ValueError('Invalid native access mode')
        ids = value.get('source_ids', [])
        if (not isinstance(ids, list) or len(ids) > 100 or any(
                not isinstance(s, str) or not 1 <= len(s) <= 1000 or any(ord(c) < 32 for c in s) for s in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Invalid native source IDs')
        helper = value.get('helper_path')
        if helper is not None and (not isinstance(helper, str)
                                   or not Path(helper).is_absolute() or any(ord(c) < 32 for c in helper)):
            raise ValueError('Invalid native helper path')


class NativeReader:
    def __init__(self, name):
        self.name = name

    def read(self, action, **fields):
        config = native_config(self.name)
        global_mode = os.environ.get('ICLOUD_MAIL_ACCESS', 'read-only')
        if global_mode not in {'disabled', 'read-only', 'write-enabled'} or global_mode == 'disabled':
            raise SafetyError('Backend access is disabled or invalid')
        if config.get('access_mode', 'read-only') == 'disabled':
            raise SafetyError('Native service access is disabled')
        if action not in READ_ACTIONS[self.name]:
            raise SafetyError('Unsupported native read action')
        if platform.system() != 'Darwin':
            raise SafetyError('Native Notes and Reminders require macOS')
        sources = config.get('source_ids', [])
        if action not in {'status', 'sources'} and not sources:
            raise SafetyError('Select exact native source IDs before reading content')
        native_selections={}
        for key in ('include_native_metadata','include_native_tags','include_native_hierarchy','include_native_children','include_native_urgent','include_native_section','include_native_early'):
            if key in fields and (self.name!='reminders' or action!='get'):raise SafetyError('Native metadata selection is supported only for Reminders get')
            value=fields.pop(key,False)
            if type(value) is not bool:raise SafetyError('Native metadata selection must be Boolean')
            if value and (config.get('advanced_backend')!='reminderkit' or not config.get('helper_path')):raise SafetyError('Native metadata reads require the explicitly selected ReminderKit backend and signed helper')
            if value:native_selections[key]=True
        flagged=fields.get('flagged')
        if flagged is not None:
            if self.name!='reminders' or action!='query' or type(flagged) is not bool:raise SafetyError('Flag filtering requires a Boolean on a Reminders query')
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):raise SafetyError('Flag filtering requires the explicitly selected ReminderKit backend and signed helper')
        urgent=fields.get('urgent')
        if urgent is not None:
            if self.name!='reminders' or action!='query' or type(urgent) is not bool:raise SafetyError('Urgent filtering requires a Boolean on a Reminders query')
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):raise SafetyError('Urgent filtering requires the explicitly selected ReminderKit backend and signed helper')
        tag=fields.get('tag')
        if tag is not None:
            try:
                if self.name!='reminders' or action!='query' or not isinstance(tag,str) or not tag or len(tag.encode('utf-8'))>512 or any(ord(c)<32 for c in tag):raise ValueError()
            except (ValueError,UnicodeEncodeError):raise SafetyError('Tag filtering requires one exact bounded native tag name') from None
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):raise SafetyError('Tag filtering requires the explicitly selected ReminderKit backend and signed helper')
        if 'source_id' in fields and action not in {'recovery_lookup','recovery_tree','recovery_list','model_folder_role','default_folder'}:raise SafetyError('Source selector is only supported for exact role or recovery lookup')
        if action=='list_sections':
            if self.name!='reminders' or set(fields)!={'list_id'} or not isinstance(fields.get('list_id'),str) or not fields['list_id']:
                raise SafetyError('Section discovery requires one exact list ID')
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):
                raise SafetyError('Section discovery requires the explicitly selected ReminderKit backend and signed helper')
        if action=='default_folder' and (self.name!='notes' or set(fields)!={'source_id'} or fields.get('source_id') not in sources):
            raise SafetyError('Default Notes folder lookup requires one exact selected source')
        if action=='model_folder_role':
            if self.name!='notes' or set(fields)!={'folder_id','source_id'} or fields.get('source_id') not in sources:
                raise SafetyError('Model role lookup requires an exact selected source and folder')
            if (config.get('folder_write_backend')!='notes-shared' or config.get('metadata_backend')!='notes-store-readonly'
                    or not config.get('helper_path')):
                raise SafetyError('Model role lookup requires explicitly selected native Notes and read-only store backends and a signed helper')
        if action=='recovery_list':
            if self.name!='reminders' or set(fields)-{'source_id','limit','offset'} or fields.get('source_id') not in sources:
                raise SafetyError('Recovery discovery requires one exact selected source and bounded page')
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):
                raise SafetyError('Recovery discovery requires the explicitly selected ReminderKit backend and signed helper')
            if type(fields.get('limit',20)) is not int or not 1<=fields.get('limit',20)<=20 or type(fields.get('offset',0)) is not int or not 0<=fields.get('offset',0)<=5000:
                raise SafetyError('Recovery discovery requires limit 1..20 and offset 0..5000')
            fields.setdefault('limit',20);fields.setdefault('offset',0)
        if action in {'recovery_lookup','recovery_tree'}:
            if self.name!='reminders' or set(fields)!={'id','source_id'} or fields.get('source_id') not in sources:
                raise SafetyError('Recovery lookup requires one exact selected source and reminder ID')
            if config.get('advanced_backend')!='reminderkit' or not config.get('helper_path'):
                raise SafetyError('Recovery lookup requires the explicitly selected ReminderKit backend and signed helper')
        if 'representation' in fields and (self.name!='notes' or action!='get'):raise SafetyError('Representation selection is supported only for Notes get')
        representation=fields.pop('representation','native')
        if (not isinstance(representation,str) or representation not in {'native','markdown'} or
                (representation!='native' and (self.name!='notes' or action!='get'))):raise SafetyError('Markdown is a read-only Notes get representation')
        match_scope=fields.pop('match_scope','all')
        if not isinstance(match_scope,str) or match_scope not in {'all','title'} or (match_scope!='all' and (self.name!='notes' or action!='search')):
            raise SafetyError('Title-only search is supported only for Notes search')
        if set(fields) - {'id', 'folder_id', 'list_id', 'limit', 'offset', 'completed', 'text', 'due_date', 'timezone', 'include_overdue', 'due_from', 'due_through', 'priorities', 'flagged', 'tag', 'urgent', 'source_id', 'modified_from', 'modified_before'}:
            raise SafetyError('Unknown native read fields')
        modified={k:fields[k] for k in ('modified_from','modified_before') if fields.get(k) is not None}
        if modified:
            import re
            try:
                if self.name!='notes' or action!='search':raise ValueError()
                for value in modified.values():
                    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):raise ValueError()
                    datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ')
                if len(modified)==2 and modified['modified_from']>=modified['modified_before']:raise ValueError()
            except (ValueError,TypeError):raise SafetyError('Notes modification bounds require ordered UTC timestamps; the upper bound is exclusive') from None
        limit, offset = fields.get('limit', 50), fields.get('offset', 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise SafetyError('Invalid bounded native page')
        for key in ('id', 'folder_id', 'list_id', 'source_id', 'text'):
            value = fields.get(key)
            minimum=0 if key=='text' and self.name=='notes' and action=='search' and match_scope=='title' else 1
            if value is not None and (not isinstance(value, str) or not minimum <= len(value) <= 1000):
                raise SafetyError('Invalid native selector')
        if fields.get('completed') is not None and type(fields['completed']) is not bool:
            raise SafetyError('Invalid completion filter')
        dates={key:fields[key] for key in ('due_date','due_from','due_through') if fields.get(key) is not None}
        if dates or fields.get('timezone') is not None:
            try:
                for value in dates.values():
                    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError()
                ZoneInfo(fields.get('timezone',''))
                if 'due_date' in dates and len(dates)>1:raise ValueError()
                if dates.get('due_from','0001-01-01')>dates.get('due_through','9999-12-31'):raise ValueError()
            except (ValueError,TypeError,ZoneInfoNotFoundError):
                raise SafetyError('Use ordered ISO dates, one daily/range filter and an explicit IANA timezone') from None
        priorities=fields.get('priorities')
        if priorities is not None and (not isinstance(priorities,list) or not 1<=len(priorities)<=4
                or any(type(p) is not int or p not in {0,1,5,9} for p in priorities) or len(set(priorities))!=len(priorities)):
            raise SafetyError('Priorities must select distinct none/high/medium/low values 0/1/5/9')
        if fields.get('include_overdue') is True and 'due_date' not in dates:
            raise SafetyError('Overdue expansion requires an exact due_date')
        if fields.get('include_overdue') is not None and type(fields['include_overdue']) is not bool:
            raise SafetyError('Invalid overdue filter')
        request = {'protocol_version': 1, 'action': action, 'source_ids': sources,
                   **{k: v for k, v in fields.items() if v is not None}}
        if match_scope=='title':request['match_scope']='title'
        if action=='model_folder_role':request['source_ids']=[fields['source_id']]
        request.update(native_selections)
        if action in {'folders','query','search','attachments','lists'}:
            request.setdefault('limit',limit);request.setdefault('offset',offset)
        if self.name=='notes' and action=='folder_role':
            if config.get('metadata_backend','scripting')!='notes-store-readonly':raise SafetyError('Optional Notes store metadata requires explicit owner configuration; no automatic fallback')
            if set(fields)!={'folder_id'}:raise SafetyError('Folder-role reads require only one exact folder ID')
            from .native_note_store import folder_role
            return folder_role(fields['folder_id'],sources)
        result = self._dispatch(request, config)
        if action=='list_sections':
            import uuid
            rows=result.get('sections') if isinstance(result,dict) else None
            if (native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                    or not isinstance(rows,list) or len(rows)>100 or result.get('source_id') not in sources or result.get('list_id')!=fields['list_id']
                    or result.get('ordering_verified') is not False or result.get('membership_verified') is not False or result.get('data_mutated') is not False):
                raise SafetyError('Scoped reminder sections could not be verified')
            seen=set()
            for row in rows:
                try:
                    if not isinstance(row,dict) or set(row)!={'id','title'} or not isinstance(row['id'],str) or not isinstance(row['title'],str) or len(row['title'])>1000:raise ValueError()
                    identity=str(uuid.UUID(row['id'])).upper()
                    if identity!=row['id'].upper() or identity in seen:raise ValueError()
                    seen.add(identity)
                except (ValueError,TypeError,AttributeError):raise SafetyError('Invalid or duplicate reminder section identity') from None
            result={key:result[key] for key in ('protocol_version','source_id','list_id','sections','ordering_verified','membership_verified','data_mutated')}
        if action=='default_folder':
            folder=result.get('folder') if isinstance(result,dict) else None
            if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                    or not isinstance(result,dict) or set(result)-{'protocol_version','request_id','nonprompting_event_count','folder'}
                    or not isinstance(folder,dict) or set(folder)!={'id','source_id','parent_id','title','shared','role'}
                    or folder['source_id']!=fields['source_id'] or folder['parent_id']!=fields['source_id']
                    or not isinstance(folder['id'],str) or not folder['id'] or not isinstance(folder['title'],str)
                    or len(folder['title'])>1000 or type(folder['shared']) is not bool or folder['role']!='default_folder'):
                raise SafetyError('Default Notes folder could not be verified in the selected source')
            result={'protocol_version':1,'folder':folder}
        if action=='default_list':
            if (native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                    or not isinstance(result,dict) or set(result)!={'protocol_version','state','list'}
                    or result.get('state') not in {'available','outside_selected_scope','unavailable'}):
                raise SafetyError('Default reminder list could not be verified in the selected scope')
            value=result.get('list')
            if result['state']=='available':
                if (not isinstance(value,dict) or set(value)!={'id','title','source_id','writable','color'}
                        or value.get('source_id') not in sources or not isinstance(value.get('id'),str)
                        or not value['id'] or not isinstance(value.get('title'),str) or len(value['title'])>1000 or type(value.get('writable')) is not bool):
                    raise SafetyError('Default reminder list has invalid or unselected identity')
                color=value['color']
                if color is not None:
                    import math
                    if (not isinstance(color,dict) or set(color)!={'red','green','blue','alpha'} or any(
                            type(channel) not in (int,float) or not math.isfinite(channel) or not 0<=channel<=1 for channel in color.values())):
                        raise SafetyError('Default reminder list has invalid color metadata')
            elif value is not None:raise SafetyError('Default reminder list exposed an unselected identity')
        if action=='model_folder_role':
            if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                    or not isinstance(result,dict) or result.get('folder_id')!=fields['folder_id'] or result.get('source_id')!=fields['source_id']
                    or result.get('role') not in {'ordinary_non_system','system_trash'} or result.get('backend')!='notes-shared-folder-role-v1'
                    or result.get('data_mutated') is not False or type(result.get('ancestor_count')) is not int or not 0<=result['ancestor_count']<32):
                raise SafetyError('Configured helper did not verify the exact Notes folder role')
        if action=='recovery_list':
            if native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode:raise SafetyError('Reminder access changed during recovery discovery')
            validate_recovery_list(result,fields['source_id'],fields['limit'],fields['offset'])
        if action=='recovery_tree':
            if native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode:raise SafetyError('Reminder access changed during recovery inspection')
            validate_recovery_tree(result,fields['id'],fields['source_id'])
        if action=='recovery_lookup':
            if native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode:
                raise SafetyError('Reminder access changed during recovery lookup')
            if (not isinstance(result,dict) or result.get('id')!=fields['id'] or result.get('source_id')!=fields['source_id']
                    or type(result.get('found_as_root')) is not bool or type(result.get('found_in_view')) is not bool
                    or 'parent_id' not in result or 'recovery_root_id' not in result
                    or (not result['found_in_view'] and (result['found_as_root'] or result['parent_id'] is not None or result['recovery_root_id'] is not None))
                    or (result['found_in_view'] and (not isinstance(result['recovery_root_id'],str) or not result['recovery_root_id']))
                    or (result['found_as_root'] and (not result['found_in_view'] or result['parent_id'] is not None or result['recovery_root_id']!=result['id']))
                    or (result['found_in_view'] and not result['found_as_root'] and (not isinstance(result['parent_id'],str) or not result['parent_id']))
                    or result.get('view')!='provider_recently_deleted'
                    or result.get('observation_consistency')!='separate_provider_reads' or result.get('data_mutated') is not False
                    or result.get('children_complete') is not False or 'reported_child_ids' not in result
                    or result.get('children_status') not in {'unavailable','provider_reported'}
                    or (result.get('children_status')=='unavailable')!=(result['reported_child_ids'] is None)
                    or (result.get('found_in_view') is False and result.get('reported_child_ids') is not None)
                    or (result.get('reported_child_ids') is not None and (not isinstance(result['reported_child_ids'],list)
                        or len(result['reported_child_ids'])>100 or any(not isinstance(x,str) or not x for x in result['reported_child_ids'])
                        or len(set(result['reported_child_ids']))!=len(result['reported_child_ids'])))):
                raise SafetyError('Configured helper did not verify the exact recovery view; update or review its compatibility')
        if native_selections:
            # Older helpers may silently ignore newer selectors. Never return a
            # successful native read without the requested, scoped coverage.
            item=result.get('item') if isinstance(result,dict) else None
            metadata=item.get('native_metadata') if isinstance(item,dict) else None
            expected=[label for key,label in (('include_native_metadata','flagged'),('include_native_tags','tags'),('include_native_hierarchy','hierarchy'),('include_native_children','children'),('include_native_urgent','urgent'),('include_native_section','section'),('include_native_early','early')) if key in native_selections]
            if (not isinstance(metadata,dict) or metadata.get('coverage')!=expected
                    or item.get('id')!=request.get('id') or item.get('source_id') not in sources
                    or not isinstance(item.get('list_id'),str) or not item['list_id']
                    or any(metadata.get(k)!=item.get(k) for k in ('id','source_id','list_id'))
                    or ('early' in expected and (not isinstance(metadata.get('early_alerts'),list) or len(metadata['early_alerts'])>100 or any(not isinstance(a,dict) or set(a)!={'native_unit','native_count'} or type(a['native_unit']) is not int or not 0<=a['native_unit']<=100 or type(a['native_count']) is not int or not -100000<=a['native_count']<=100000 for a in metadata['early_alerts']) or 'early_payload_fingerprint' not in metadata or (metadata['early_payload_fingerprint'] is not None and (not isinstance(metadata['early_payload_fingerprint'],str) or len(metadata['early_payload_fingerprint'])!=64 or any(c not in '0123456789abcdef' for c in metadata['early_payload_fingerprint']))) or (metadata['early_payload_fingerprint'] is None and bool(metadata['early_alerts']))))
                    or ('urgent' in expected and type(metadata.get('urgent')) is not bool)
                    or ('section' in expected and ('section' not in metadata or metadata.get('section_status') not in {'provider_reported','provider_not_reported'}
                        or (metadata.get('section_status')=='provider_not_reported' and metadata.get('section') is not None)
                        or (metadata.get('section_status')=='provider_reported' and (not isinstance(metadata.get('section'),dict)
                            or set(metadata['section'])!={'id','title'} or not isinstance(metadata['section'].get('id'),str)
                            or not metadata['section']['id'] or not isinstance(metadata['section'].get('title'),str) or len(metadata['section']['title'])>1000))))
                    or ('flagged' in expected and (type(metadata.get('flagged')) is not bool or type(metadata.get('flag_state')) is not int or metadata.get('flag_state') not in (0,1) or metadata['flagged']!=(metadata['flag_state']==1)))
                    or ('tags' in expected and (not isinstance(metadata.get('tags'),list) or len(metadata['tags'])>200 or metadata.get('tags_complete') is not True or any(not isinstance(t,dict) or not isinstance(t.get('id'),str) or not isinstance(t.get('name'),str) or type(t.get('native_type')) is not int for t in metadata['tags'])))
                    or ('hierarchy' in expected and (type(metadata.get('is_subtask')) is not bool or 'parent_id' not in metadata or 'has_subtasks' not in metadata or metadata.get('hierarchy_complete') is not False or (metadata.get('has_subtasks') is not None and type(metadata.get('has_subtasks')) is not bool) or not isinstance(metadata.get('children_status'),str) or metadata.get('children_status') not in {'unavailable','provider_reported'} or (metadata.get('children_status')=='unavailable')!=(metadata.get('has_subtasks') is None) or (metadata.get('is_subtask') and not isinstance(metadata.get('parent_id'),str)) or (not metadata.get('is_subtask') and metadata.get('parent_id') is not None)))
                    or ('children' in expected and (metadata.get('children_complete') is not False or 'child_ids' not in metadata or not isinstance(metadata.get('children_fetch_status'),str) or metadata.get('children_fetch_status') not in {'unavailable','provider_reported'} or (metadata.get('children_fetch_status')=='unavailable')!=(metadata.get('child_ids') is None) or (metadata.get('child_ids') is not None and (not isinstance(metadata.get('child_ids'),list) or len(metadata['child_ids'])>100 or any(not isinstance(x,str) or not x for x in metadata['child_ids']) or len(set(metadata['child_ids']))!=len(metadata['child_ids'])))))):
                raise SafetyError('Configured helper did not provide the requested scoped native metadata; update or review its compatibility')
            if 'section' in expected and metadata['section_status']=='provider_reported':
                import uuid
                try:
                    value=metadata['section']['id']
                    if str(uuid.UUID(value)).upper()!=value.upper():raise ValueError()
                except (ValueError,AttributeError):raise SafetyError('Configured helper returned an invalid section identity') from None
        if flagged is not None or tag is not None or urgent is not None:
            items=result.get('items') if isinstance(result,dict) else None
            keys=(['native_flag_filter'] if flagged is not None else [])+(['native_tag_filter'] if tag is not None else [])+(['native_urgent_filter'] if urgent is not None else [])
            evidence=result.get(keys[0]) if isinstance(result,dict) else None
            expected=(['flagged'] if flagged is not None else [])+(['tags'] if tag is not None else [])+(['urgent'] if urgent is not None else [])
            if (not isinstance(items,list) or not isinstance(evidence,dict)
                    or any(result.get(key)!=evidence for key in keys)
                    or type(evidence.get('scanned')) is not int or not 0<=evidence['scanned']<=limit
                    or type(evidence.get('matched')) is not int or evidence['matched']!=len(items) or len(items)>evidence['scanned']
                    or evidence.get('observation_consistency')!='separate_provider_reads' or result.get('pagination_consistency')!='live_offset_may_shift'
                    or type(result.get('done')) is not bool or type(result.get('next_offset')) is not int
                    or result['next_offset']!=offset+evidence['scanned'] or (not result['done'] and evidence['scanned']==0)
                    or any(not isinstance(x,dict) or not isinstance(x.get('native_metadata'),dict)
                        or not isinstance(x.get('id'),str) or not x['id'] or not isinstance(x.get('list_id'),str) or not x['list_id']
                        or x.get('source_id') not in sources or (fields.get('list_id') is not None and x.get('list_id')!=fields['list_id'])
                        or any(x['native_metadata'].get(k)!=x.get(k) for k in ('id','source_id','list_id'))
                        or x['native_metadata'].get('coverage')!=expected
                        or (flagged is not None and x['native_metadata'].get('flagged') is not flagged)
                        or (urgent is not None and x['native_metadata'].get('urgent') is not urgent)
                        or (tag is not None and (x['native_metadata'].get('tags_complete') is not True
                            or not isinstance(x['native_metadata'].get('tags'),list) or len(x['native_metadata']['tags'])>200
                            or any(not isinstance(t,dict) or not isinstance(t.get('id'),str) or not isinstance(t.get('name'),str) or type(t.get('native_type')) is not int for t in x['native_metadata']['tags'])
                            or not any(isinstance(t,dict) and t.get('name')==tag for t in x['native_metadata']['tags']))) for x in items)):
                raise SafetyError('Configured helper did not verify the requested native filters; update or review its compatibility')
        if self.name=='notes' and action=='status' and config.get('folder_write_backend')=='notes-shared':
            if not config.get('helper_path'):raise SafetyError('Configure the signed helper for optional native Notes store status')
            if native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode:raise SafetyError('Native access changed before optional store status')
            probe={'protocol_version':1,'action':'store_status','source_ids':sources}
            result['native_store']=self._dispatch(probe,config)
        if self.name == 'notes' and action == 'search':
            from .notes_content import project_html
            candidates=result.pop('table_projection_candidates',[])
            if not isinstance(candidates,list) or len(candidates)>limit:
                raise SafetyError('Invalid bounded Notes search projection')
            if match_scope=='title':
                if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                        or result.get('match_scope')!='title' or candidates or not isinstance(result.get('items'),list)
                        or any(not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']
                               or row.get('source_id') not in sources or not isinstance(row.get('folder_id'),str) or not row['folder_id']
                               or (fields.get('folder_id') is not None and row['folder_id']!=fields['folder_id'])
                               or not isinstance(row.get('title'),str) or fields['text'].lower() not in row['title'].lower()
                               or type(row.get('locked')) is not bool or row.get('content_complete') is not False
                               or row.get('excerpt')!='' for row in result['items'])):
                    raise SafetyError('Configured helper did not verify title-only Notes search')
            if modified:
                if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=global_mode
                        or result.get('modified_filter')!={'from':modified.get('modified_from'),'before':modified.get('modified_before'),'semantics':'utc_inclusive_from_exclusive_before'}
                        or not isinstance(result.get('items'),list)):
                    raise SafetyError('Configured helper did not verify the requested Notes modification filter')
                for row in result['items']+candidates:
                    stamp=row.get('modified') if isinstance(row,dict) else None
                    try:
                        if (not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']
                                or row.get('source_id') not in sources or not isinstance(row.get('folder_id'),str) or not row['folder_id']
                                or (fields.get('folder_id') is not None and row['folder_id']!=fields['folder_id'])):raise ValueError()
                        if not isinstance(stamp,str) or len(stamp)!=24 or datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%S.%fZ') is None:raise ValueError()
                        if (modified.get('modified_from') and stamp<modified['modified_from'][:-1]+'.000Z') or (modified.get('modified_before') and stamp>=modified['modified_before'][:-1]+'.000Z'):raise ValueError()
                    except ValueError:raise SafetyError('Configured helper returned a note outside the requested modification range') from None
            for candidate in candidates:
                try:
                    projected=project_html(candidate['html'])
                    index=projected['text'].lower().find(fields['text'].lower())
                    if index>=0:
                        result.setdefault('items',[]).append({key:candidate[key] for key in ('id','title','source_id','folder_id')})
                        result['items'][-1].update(excerpt=projected['text'][max(0,index-80):index+240],
                            locked=False,content_complete=False,match_source='exported_table_text',
                            text_projection_complete=projected['text_projection_complete'] and not candidate.get('truncated',False))
                        if modified:result['items'][-1]['modified']=candidate['modified']
                except (ValueError,KeyError,TypeError):
                    result.setdefault('unavailable',[]).append({'category':'table_projection_unavailable'})
            if len(result.get('items',[]))>limit:raise SafetyError('Native search exceeded result limit')
            if match_scope=='title':
                unavailable=result.get('unavailable',[])
                if (not isinstance(unavailable,list) or len(unavailable)>100 or any(not isinstance(row,dict)
                        or not isinstance(row.get('id'),str) or not row['id']
                        or row.get('category') not in {'note_read_unavailable','modification_date_unavailable'} for row in unavailable)
                        or ('pagination_consistency' in result and result['pagination_consistency']!='live_offset_may_shift')
                        or ('done' in result and type(result['done']) is not bool)
                        or any(k in result and (type(result[k]) is not int or result[k]<0) for k in ('next_offset','scanned','locked_bodies_excluded','truncated_bodies'))):
                    raise SafetyError('Configured helper returned invalid title-only search diagnostics')
                allowed=('id','title','source_id','folder_id','locked','excerpt','content_complete','modified')
                result['items']=[{key:row[key] for key in allowed if key in row} for row in result['items']]
                result['unavailable']=[{'id':row['id'],'category':row['category']} for row in unavailable]
                keys=('items','unavailable','done','next_offset','scanned','locked_bodies_excluded','truncated_bodies','pagination_consistency','match_scope','modified_filter')
                result={key:result[key] for key in keys if key in result}
                result['search_coverage']='bounded title only; no note bodies read'
        if self.name == 'notes' and action == 'get' and 'html' in result.get('item', {}):
            from .notes_content import project_html
            try:
                projection = project_html(result['item']['html'])
                result['item']['derived_text'] = projection['text']
                result['item']['native_table_exports'] = projection['tables']
                result['item']['text_projection_complete'] = projection['text_projection_complete'] and not result['item'].get('truncated')
            except ValueError:
                result['item']['text_projection_complete'] = False
                result['item']['projection_error'] = 'unsupported_html_structure'
        if self.name=='notes' and action=='get' and representation=='markdown':
            item=result.get('item',{})
            if isinstance(item.get('html'),str):
                from .notes_markdown import project_markdown
                try:
                    projection=project_markdown(item['html'],source_truncated=bool(item.get('truncated')))
                    item['markdown']=projection.pop('text');item['markdown_projection']=projection
                except ValueError:item['markdown_projection']={'status':'unavailable','reason':'unsupported_or_oversized_html','round_trip_safe':False}
            else:item['markdown_projection']={'status':'unavailable','reason':'native_html_unavailable','round_trip_safe':False}
        return result

    def _dispatch(self, request, config):
        """Internal transport; callers enforce fixed read or approved-write actions."""
        action = request["action"]
        if self.name == 'reminders' or config.get('helper_path'):
            request['service'] = self.name
            helper = config.get('helper_path')
            if not helper:
                raise SafetyError('Native Reminders helper is not configured')
            path = Path(helper)
            if path.is_symlink() or not path.is_file():
                raise SafetyError('Native helper must be a regular executable')
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o022 or not os.access(path, os.X_OK):
                raise SafetyError('Native helper must be owner-controlled and executable')
            if self.name == 'notes':
                from .native_app import application_request,bundle_fingerprint
                readonly=action in READ_ACTIONS['notes']
                fingerprint=bundle_fingerprint(path) if readonly else None
                prior_mode=os.environ.get('ICLOUD_MAIL_ACCESS','read-only')
                response = application_request(path, request, timeout=75 if action=="request_access" else 40)
                if readonly and response.get('error')=='notes_operation_failed' and response.get('error_code')==-1712:
                    # Only a completed, fixed read can repeat once. Never retry
                    # mutation, consent, missing response or ambiguous dispatch.
                    if (native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=prior_mode
                            or prior_mode=='disabled' or bundle_fingerprint(path)!=fingerprint):
                        raise SafetyError('Native access or helper changed before the read-only retry')
                    response=application_request(path,request,timeout=40)
                    response=dict(response,read_retry_count=1)
                if 'error' in response:
                    if action=='model_folder_role' and response.get('category')=='notes_model_store_unavailable':
                        raise SafetyError('Optional Notes native model store is unavailable to the configured helper; no access request or fallback was made')
                    if action=='apply' and response.get('mutation_dispatched') is False:
                        raise _confirmed_refusal(self.name,request,response)
                    if response['error']=='permission_check_timed_out':
                        raise SafetyError('Notes permission check timed out; OS access is unverified. No automatic retry.')
                    if response['error']=='notes_not_running':
                        raise SafetyError('Open Notes before validating native Automation access')
                    if response['error']=='permission_required':
                        raise SafetyError('Notes Automation permission is required for the native helper app')
                    code=response.get('error_code')
                    detail=(' (Apple event code '+str(code)+')') if type(code) is int else ''
                    if response.get('stage') in {'validation','folder_lookup','membership_before','note_constructor','note_insert','membership_after','note_move'}:detail+=' at '+response['stage']
                    raise SafetyError('Native Notes operation failed'+detail+'; no automatic retry')
                return response
            command = [str(path)]
        else:
            if action == 'status':
                return {'protocol_version': 1, 'service': 'notes', 'os_permission': 'not_checked',
                        'reason': 'Configure the native helper for a nonprompting permission check'}
            command = ['/usr/bin/osascript', '-l', 'JavaScript',
                       str(Path(__file__).parent / 'native/notes.js')]
        # Temporary files bound memory and keep potentially private output out of logs.
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'TMPDIR', 'LANG') if k in os.environ}
        try:
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                encoded = json.dumps(request, ensure_ascii=False).encode()
                if len(encoded) > 65536:
                    raise SafetyError('Native request exceeds input limit')
                process = subprocess.run(command, input=encoded, stdout=output, stderr=errors,
                               timeout=75 if action=="request_access" else 40, check=False, env=env)
                if process.returncode:
                    errors.seek(0)
                    diagnostic = errors.read(8192)
                    if b"(-1743)" in diagnostic or b"(-1744)" in diagnostic:
                        raise SafetyError("Notes Automation permission is required; the user must approve access in macOS Privacy & Security. No automatic retry.")
                    raise SafetyError("Native helper failed; no automatic retry.")
                output.seek(0)
                raw = output.read(MAX_OUTPUT + 1)
            if len(raw) > MAX_OUTPUT:
                raise SafetyError('Native response exceeds limit; narrow the query')
            response = json.loads(raw)
        except SafetyError:
            raise
        except (subprocess.SubprocessError, OSError, ValueError):
            raise SafetyError('Native read failed; inspect OS permission and helper configuration. No automatic retry.') from None
        if not isinstance(response, dict) or response.get('protocol_version') != 1:
            raise SafetyError('Invalid native helper response')
        if 'error' in response:
            if action=='apply' and response.get('mutation_dispatched') is False:
                raise _confirmed_refusal(self.name,request,response)
            code = response['error']
            allowed = {'permission_required', 'not_found_in_scope', 'source_scope_required',
                       'invalid_page', 'response_limit', 'read_failed', 'read_timeout', 'query_limit','private_metadata_unavailable'}
            if self.name == 'notes' and code == 'permission_required':
                raise SafetyError('Notes Automation permission is required; the user must approve access in macOS Privacy & Security.')
            detail=''
            if code=='private_metadata_unavailable' and response.get('category') in {'unsupported_public_identity','unsupported_interface','private_store_unavailable','private_read_denied_or_unavailable','private_reminder_not_found','native_identity_mismatch','unsupported_flag_metadata','unsupported_tag_metadata','unsupported_hierarchy_metadata','unsupported_children_metadata','children_read_unavailable','hierarchy_parent_unavailable','hierarchy_parent_inconsistent','hierarchy_read_unavailable','unknown_flag_state','private_read_exception'}:detail=' ('+response['category']+')'
            if code=='private_metadata_unavailable' and response.get('native_error_domain') in {'com.apple.reminderkit','NSCocoaErrorDomain','NSPOSIXErrorDomain','EKErrorDomain','NSOSStatusErrorDomain','other'} and type(response.get('native_error_code')) is int:detail+=' [SDK '+response['native_error_domain']+' '+str(response['native_error_code'])+']'
            raise SafetyError('Native read refused: ' + (code if code in allowed else 'helper_error')+detail)
        return response


def inspect_reminder_hierarchy(reader, identity, max_nodes=10, max_depth=3):
    """Inspect provider-reported edges without claiming a complete subtree."""
    if not isinstance(identity,str) or not identity or len(identity)>1000:
        raise SafetyError('Select one exact reminder ID')
    if type(max_nodes) is not int or not 1<=max_nodes<=20 or type(max_depth) is not int or not 0<=max_depth<=5:
        raise SafetyError('Hierarchy inspection requires max_nodes 1..20 and max_depth 0..5')
    initial_config=native_config('reminders')
    initial_mode=os.environ.get('ICLOUD_MAIL_ACCESS','read-only')
    pending=[(identity,None,0)];seen={identity};nodes=[];unexpanded=[]
    source=list_id=None
    while pending:
        current,parent,depth=pending.pop(0)
        if native_config('reminders')!=initial_config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=initial_mode:
            raise SafetyError('Reminder access changed during hierarchy inspection')
        item=reader.read('get',id=current,include_native_hierarchy=True,include_native_children=True)['item']
        metadata=item['native_metadata']
        title=item.get('title');completed=item.get('completed')
        if not isinstance(title,str) or type(completed) is not bool:
            raise SafetyError('Hierarchy item title or completion state is unavailable')
        if source is None:source,list_id=item['source_id'],item['list_id']
        if item['source_id']!=source or item['list_id']!=list_id:
            raise SafetyError('Hierarchy crossed the selected source or list')
        if parent is not None and (metadata['is_subtask'] is not True or metadata['parent_id']!=parent):
            raise SafetyError('Hierarchy changed or a child has a different parent')
        children=metadata['child_ids']
        nodes.append({'id':current,'title':title[:1000],'title_truncated':len(title)>1000,'completed':completed,'depth':depth,'parent_id':metadata['parent_id'],
                      'is_subtask':metadata['is_subtask'],'has_subtasks':metadata['has_subtasks'],
                      'child_ids':children,'children_fetch_status':metadata['children_fetch_status']})
        if children is None:
            unexpanded.append({'id':current,'reason':'provider_unavailable'})
            continue
        for child in children:
            if child in seen:raise SafetyError('Hierarchy contains a cycle or duplicate child identity')
            seen.add(child)
            if depth>=max_depth:unexpanded.append({'id':child,'parent_id':current,'reason':'depth_limit'})
            elif len(nodes)+len(pending)>=max_nodes:unexpanded.append({'id':child,'parent_id':current,'reason':'node_limit'})
            else:pending.append((child,current,depth+1))
    if native_config('reminders')!=initial_config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=initial_mode:
        raise SafetyError('Reminder access changed during hierarchy inspection')
    return {'root_id':identity,'source_id':source,'list_id':list_id,'nodes':nodes,'unexpanded':unexpanded,
            'hierarchy_complete':False,'observation_consistency':'separate_provider_reads',
            'scope_note':'Provider-reported edges only; concealed descendants and concurrent changes remain unverified.'}


def validate_recovery_list(result,source_id,limit,offset):
    import uuid
    if (not isinstance(result,dict) or result.get('source_id')!=source_id or result.get('view')!='provider_recently_deleted_roots'
            or result.get('pagination_consistency')!='live_offset_may_shift' or result.get('data_mutated') is not False
            or type(result.get('done')) is not bool or type(result.get('next_offset')) is not int):
        raise SafetyError('Helper did not provide scoped recovery discovery')
    items=result.get('items')
    if not isinstance(items,list) or len(items)>limit or result['next_offset']!=offset+len(items) or (not result['done'] and len(items)!=limit):raise SafetyError('Invalid recovery discovery page')
    seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'id','source_id','title'} or item['source_id']!=source_id or not isinstance(item['title'],str) or len(item['title'])>1000:raise SafetyError('Invalid recovery discovery item')
        try:identity=str(uuid.UUID(item['id'])).upper()
        except (ValueError,TypeError,AttributeError):raise SafetyError('Invalid recovery identity') from None
        if identity in seen:raise SafetyError('Duplicate recovery identity')
        seen.add(identity)
    return result


def validate_recovery_tree(result,identity,source_id):
    """Reject malformed or unscoped single-view evidence; no completeness inference."""
    import uuid
    def canonical(value):
        try:
            if not isinstance(value,str):raise ValueError()
            return str(uuid.UUID(value)).upper()
        except (ValueError,AttributeError):raise SafetyError('Invalid recovery hierarchy identity') from None
    if (not isinstance(result,dict) or result.get('source_id')!=source_id or result.get('requested_id')!=identity
            or result.get('view')!='provider_recently_deleted' or result.get('data_mutated') is not False
            or result.get('hierarchy_complete') is not False or result.get('observation_consistency')!='single_provider_view'
            or result.get('title_observation_consistency')!='separate_object_reads'):
        raise SafetyError('Helper did not provide scoped single-view recovery evidence')
    root=canonical(result.get('root_id'));selected=canonical(identity);nodes=result.get('nodes')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=100:raise SafetyError('Recovery hierarchy is empty or exceeds the bound')
    indexed={}
    for node in nodes:
        if not isinstance(node,dict) or set(node)!={'id','title','parent_id','depth','reported_child_ids','children_status'} or not isinstance(node.get('title'),str) or len(node['title'])>1000:raise SafetyError('Invalid recovery hierarchy node')
        identity_key=canonical(node['id']);parent=canonical(node['parent_id']) if node['parent_id'] is not None else None
        depth=node['depth'];children=node['reported_child_ids'];status=node['children_status']
        if identity_key in indexed or type(depth) is not int or not 0<=depth<=10 or status not in {'unavailable','provider_reported'} or (status=='unavailable')!=(children is None):raise SafetyError('Invalid recovery hierarchy node')
        if children is not None:
            if not isinstance(children,list) or len(children)>100:raise SafetyError('Invalid recovery hierarchy children')
            children=[canonical(child) for child in children]
            if len(set(children))!=len(children):raise SafetyError('Duplicate recovery hierarchy child')
        indexed[identity_key]=(parent,depth,children)
    if root not in indexed or selected not in indexed or indexed[root][:2]!=(None,0):raise SafetyError('Recovery root or requested identity is missing')
    for key,(parent,depth,children) in indexed.items():
        if key!=root and (parent not in indexed or indexed[parent][1]+1!=depth or indexed[parent][2] is None or key not in indexed[parent][2]):raise SafetyError('Recovery hierarchy parent relationship is inconsistent')
        if children is not None and any(child not in indexed or indexed[child][0]!=key for child in children):raise SafetyError('Recovery hierarchy child relationship is inconsistent')
    return result


def inspect_reminder_recovery_hierarchy(reader, identity, source_id, max_nodes=10, max_depth=3):
    """Inspect one recovery root and provider-reported descendants, without writing."""
    if not isinstance(identity,str) or not identity or len(identity)>1000 or not isinstance(source_id,str) or not source_id:
        raise SafetyError('Select one exact reminder and source')
    if type(max_nodes) is not int or not 1<=max_nodes<=20 or type(max_depth) is not int or not 0<=max_depth<=5:
        raise SafetyError('Recovery inspection requires max_nodes 1..20 and max_depth 0..5')
    config=native_config('reminders');mode=os.environ.get('ICLOUD_MAIL_ACCESS','read-only')
    selected=reader.read('recovery_lookup',id=identity,source_id=source_id)
    if selected['found_in_view'] is not True:raise SafetyError('Reminder is not present in the observed recovery view')
    root=selected['recovery_root_id'];pending=[(root,None,0)];seen={root.upper()};nodes=[];unexpanded=[]
    while pending:
        if native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=mode:
            raise SafetyError('Reminder access changed during recovery inspection')
        current,parent,depth=pending.pop(0)
        node=selected if not nodes and current.upper()==identity.upper() else reader.read('recovery_lookup',id=current,source_id=source_id)
        if (node['found_in_view'] is not True or node['recovery_root_id'].upper()!=root.upper()
                or (parent is None and not node['found_as_root'])
                or (parent is not None and (node['parent_id'] is None or node['parent_id'].upper()!=parent.upper()))):
            raise SafetyError('Recovery hierarchy changed or a node has a different parent or root')
        children=node['reported_child_ids']
        nodes.append({'id':current,'parent_id':parent,'depth':depth,'reported_child_ids':children,'children_status':node['children_status']})
        if children is None:
            unexpanded.append({'id':current,'reason':'provider_unavailable'});continue
        for child in children:
            canonical=child.upper()
            if canonical in seen:raise SafetyError('Recovery hierarchy contains a duplicate or cycle')
            seen.add(canonical)
            if depth>=max_depth:unexpanded.append({'id':child,'parent_id':current,'reason':'depth_limit'})
            elif len(nodes)+len(pending)>=max_nodes:unexpanded.append({'id':child,'parent_id':current,'reason':'node_limit'})
            else:pending.append((child,current,depth+1))
    if native_config('reminders')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=mode:
        raise SafetyError('Reminder access changed during recovery inspection')
    return {'requested_id':identity,'source_id':source_id,'recovery_root_id':root,'nodes':nodes,'unexpanded':unexpanded,
            'hierarchy_complete':False,'observation_consistency':'separate_provider_reads',
            'scope_note':'Provider-reported recovery hierarchy only; concealed descendants and concurrent changes remain unverified.'}


def locate_notes_by_id(reader, identity, source_id, limit=10, offset=0):
    """Check one page of selected-source folders for an exact note ID."""
    if not isinstance(identity,str) or not 1<=len(identity)<=1000 or not isinstance(source_id,str) or not 1<=len(source_id)<=1000:
        raise SafetyError('Select one exact note ID and source ID')
    if type(limit) is not int or not 1<=limit<=20 or type(offset) is not int or not 0<=offset<=100000:
        raise SafetyError('Note location requires limit 1..20 and a bounded offset')
    config=native_config('notes');mode=os.environ.get('ICLOUD_MAIL_ACCESS','read-only')
    if source_id not in config.get('source_ids',[]):raise SafetyError('Note source is outside the selected scope')
    page=reader.read('folders',limit=limit,offset=offset)
    folders=page.get('folders');done=page.get('done');next_offset=page.get('next_offset')
    if (not isinstance(folders,list) or len(folders)>limit or type(done) is not bool or type(next_offset) is not int
            or next_offset!=offset+len(folders) or (not done and not folders)):
        raise SafetyError('Folder discovery returned incomplete pagination')
    found=[];seen=set()
    for folder in folders:
        if not isinstance(folder,dict) or not isinstance(folder.get('id'),str) or not folder['id'] or not isinstance(folder.get('source_id'),str) or not isinstance(folder.get('title'),str) or len(folder['title'])>1000:
            raise SafetyError('Folder discovery returned invalid identity')
        if folder['id'] in seen:raise SafetyError('Folder discovery returned a duplicate identity')
        seen.add(folder['id'])
        if folder['source_id']!=source_id:continue
        if native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=mode:
            raise SafetyError('Notes access changed during location inspection')
        result=reader.read('contains',folder_id=folder['id'],id=identity)
        if (type(result.get('exists')) is not bool or result.get('source_id')!=source_id or result.get('folder_id')!=folder['id']):
            raise SafetyError('Notes membership could not be verified in the selected folder')
        if result['exists']:
            found.append({'id':folder['id'],'source_id':source_id,'title':folder.get('title'),'role':'unverified'})
    if native_config('notes')!=config or os.environ.get('ICLOUD_MAIL_ACCESS','read-only')!=mode:
        raise SafetyError('Notes access changed during location inspection')
    return {'id':identity,'source_id':source_id,'folders':found,'scanned_folders':len(folders),
            'done':done,'next_offset':next_offset,'pagination_consistency':'live_offset_may_shift',
            'scope_note':'Exact membership only; folder role and recoverability are not established.'}


def register_read_tools(mcp):
    """Register only selected services. Dispatch checks owner settings again."""
    from functools import wraps
    from fastmcp.exceptions import ToolError
    annotations = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': True}

    def guard(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (SafetyError, ValueError) as exc:
                raise ToolError(str(exc)) from None
            except Exception:
                raise ToolError('Native read failed; no automatic retry.') from None
        return wrapped

    if enabled('reminders'):
        @mcp.tool(annotations=annotations)
        @guard
        def reminders_access_status() -> dict:
            """Inspect effective service scope and approval mode without contacting Apple."""
            from .native_access import access_status
            return access_status('reminders')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_status() -> dict:
            """Inspect existing OS grant without requesting permission or reading content."""
            return NativeReader('reminders').read('status')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_sources() -> dict:
            """Discover native source identifiers for explicit owner scope selection."""
            return NativeReader('reminders').read('sources')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_lists(limit: int = 100, offset: int = 0) -> dict:
            """List only the selected native sources' reminder lists."""
            return NativeReader('reminders').read('lists', limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_default_list() -> dict:
            """Read the system default reminder list if its source is selected; otherwise return only its scope status."""
            return NativeReader('reminders').read('default_list')

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_list_sections(list_id: str) -> dict:
            """Read bounded native section IDs and titles for one selected list; ordering and membership are not verified."""
            return NativeReader('reminders').read('list_sections',list_id=list_id)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_query(limit: int = 50, offset: int = 0, list_id: str | None = None,
                            completed: bool | None = None, text: str | None = None,
                            due_from: str | None = None, due_through: str | None = None,
                            timezone: str | None = None, priorities: list[int] | None = None, flagged: bool | None = None, tag: str | None = None, urgent: bool | None = None) -> dict:
            """Bounded tasks with optional date, priority, native flag, urgent state and exact tag filters. Follow next_offset even on empty filtered pages."""
            return NativeReader('reminders').read('query',limit=limit,offset=offset,list_id=list_id,
                completed=completed,text=text,due_from=due_from,due_through=due_through,timezone=timezone,priorities=priorities,flagged=flagged,tag=tag,urgent=urgent)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_daily(date: str, timezone: str, include_overdue: bool = True,
                            limit: int = 50, offset: int = 0, flagged: bool | None = None, list_id: str | None = None, tag: str | None = None, urgent: bool | None = None) -> dict:
            """Read incomplete reminders due on a date, optionally overdue, flagged, urgent, tagged, or in one list. Follow next_offset until done; no scheduling or delivery."""
            return NativeReader('reminders').read('query', due_date=date, timezone=timezone,
                include_overdue=include_overdue, completed=False, limit=limit, offset=offset, flagged=flagged, list_id=list_id, tag=tag, urgent=urgent)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_get(id: str, include_native_metadata: bool=False, include_native_tags: bool=False, include_native_hierarchy: bool=False, include_native_children: bool=False, include_native_urgent: bool=False, include_native_section: bool=False, include_native_early: bool=False) -> dict:
            """Read one exact scoped reminder. Optional native flags/tags/hierarchy/children/urgent/section/early state require owner-selected ReminderKit; other advanced metadata remains incomplete."""
            result=NativeReader('reminders').read('get', id=id, include_native_metadata=include_native_metadata, include_native_tags=include_native_tags, include_native_hierarchy=include_native_hierarchy, include_native_children=include_native_children, include_native_urgent=include_native_urgent,include_native_section=include_native_section,include_native_early=include_native_early)
            if include_native_early:
                # These mappings were observed on the supported macOS version;
                # leave other provider values uninterpreted rather than guessing.
                units={0:'minutes',1:'hours',2:'days'}
                alerts=result['item']['native_metadata']['early_alerts']
                result['item']['early_reminders']=[{'unit':units.get(a['native_unit']), 'offset':a['native_count'], 'native_unit':a['native_unit']} for a in alerts]
            return result

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_inspect_recovery_hierarchy(id: str, source_id: str, max_nodes: int=10, max_depth: int=3) -> dict:
            """Inspect one recoverable root and provider-reported descendants; this is not a restoration or a complete subtree."""
            return inspect_reminder_recovery_hierarchy(NativeReader('reminders'),id,source_id,max_nodes,max_depth)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_recovery_list(source_id: str, limit: int=20, offset: int=0) -> dict:
            """List bounded title-only roots in one selected account's recovery view. Pagination is live; restoration is not provided."""
            return NativeReader('reminders').read('recovery_list',source_id=source_id,limit=limit,offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_recovery_tree(id: str, source_id: str) -> dict:
            """Inspect one exact recovery root and its provider-reported hierarchy in a single bounded view. Completeness is not established."""
            return NativeReader('reminders').read('recovery_tree',id=id,source_id=source_id)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_recovery_lookup(id: str, source_id: str) -> dict:
            """Check one exact root in the selected account's Recently Deleted view; no deletion or restoration."""
            return NativeReader('reminders').read('recovery_lookup',id=id,source_id=source_id)

        @mcp.tool(annotations=annotations)
        @guard
        def reminders_inspect_hierarchy(id: str, max_nodes: int=10, max_depth: int=3) -> dict:
            """Inspect bounded provider-reported subtask edges. This is not proof of a complete subtree or authority to move/delete."""
            return inspect_reminder_hierarchy(NativeReader('reminders'),id,max_nodes,max_depth)

    if enabled('notes'):
        @mcp.tool(annotations=annotations)
        @guard
        def notes_model_folder_role(folder_id: str, source_id: str) -> dict:
            """Inspect an exact folder's native ordinary/trash role with explicitly enabled advanced Notes store access; no mutation or permission prompt. An unavailable or unsupported model refuses."""
            return NativeReader('notes').read('model_folder_role',folder_id=folder_id,source_id=source_id)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_folder_role(folder_id:str)->dict:
            """Read exact selected-folder storage role through explicitly opted-in read-only metadata; no note bodies or database writes. Default-folder status requires scripting verification."""
            return NativeReader('notes').read('folder_role',folder_id=folder_id)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_access_status() -> dict:
            """Inspect effective service scope and approval mode without requesting OS access."""
            from .native_access import access_status
            return access_status('notes')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_status() -> dict:
            """Check Notes Automation without requesting consent. With the explicitly selected native writer, also probe fixed read-only store metadata; no model context or writes."""
            return NativeReader('notes').read('status')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_sources() -> dict:
            """Discover Notes account IDs after the user grants OS Automation access."""
            return NativeReader('notes').read('sources')

        @mcp.tool(annotations=annotations)
        @guard
        def notes_default_folder(source_id: str) -> dict:
            """Read the default folder in one exact selected Notes account; does not change settings or content."""
            return NativeReader('notes').read('default_folder',source_id=source_id)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_locate(id: str, source_id: str, limit: int=10, offset: int=0) -> dict:
            """Find one exact note ID in a bounded page of selected-source folders; folder role and recoverability remain unverified."""
            return locate_notes_by_id(NativeReader('notes'),id,source_id,limit,offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_folders(limit: int = 100, offset: int = 0) -> dict:
            """Read folder identifiers only within explicitly selected accounts."""
            return NativeReader('notes').read('folders', limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_query(folder_id: str, limit: int = 50, offset: int = 0) -> dict:
            """Read a bounded title page in one exact physical folder; follow next_offset even on empty pages."""
            return NativeReader('notes').read('query', folder_id=folder_id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_search(text: str, folder_id: str | None = None, limit: int = 50, offset: int = 0,
                         modified_from: str | None = None, modified_before: str | None = None,
                         match_scope: Literal['all','title'] = 'all') -> dict:
            """Search bounded title, plaintext and exported table text, or titles only without reading bodies. An empty title-only query lists scoped notes. UTC bounds are inclusive from and exclusive before; follow next_offset even after empty pages."""
            return NativeReader('notes').read('search', text=text, folder_id=folder_id, limit=limit, offset=offset, modified_from=modified_from, modified_before=modified_before,match_scope=match_scope)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_attachments(folder_id: str, id: str, limit: int = 50, offset: int = 0) -> dict:
            """Read scoped attachment metadata only; no file download or content changes."""
            return NativeReader('notes').read('attachments', folder_id=folder_id, id=id, limit=limit, offset=offset)

        @mcp.tool(annotations=annotations)
        @guard
        def notes_read_text(folder_id: str, id: str, offset: int=0, limit: int=10000) -> dict:
            """Read a bounded page of the note's exported plaintext. Follow next_offset; a truncated source cannot be paged beyond its observed prefix."""
            if type(offset) is not int or not 0<=offset<=100000 or type(limit) is not int or not 1<=limit<=10000:
                raise SafetyError('Text pages require offset 0..100000 and limit 1..10000')
            result=NativeReader('notes').read('get',folder_id=folder_id,id=id)
            item=result.get('item')
            if not isinstance(item,dict) or item.get('id')!=id:raise SafetyError('Exact note identity was not verified')
            if item.get('locked') is True:
                return {'id':id,'folder_id':folder_id,'locked':True,'content_available':False,'data_mutated':False}
            if item.get('folder_id')!=folder_id or not isinstance(item.get('source_id'),str) or not isinstance(item.get('text'),str) or type(item.get('truncated')) is not bool:
                raise SafetyError('Plaintext availability and scope could not be verified')
            text=item['text']
            if offset>len(text):raise SafetyError('Text offset is beyond the observed note content')
            page=text[offset:offset+limit];next_offset=offset+len(page);source_truncated=item['truncated']
            return {'id':id,'folder_id':folder_id,'source_id':item['source_id'],'locked':False,'content_available':True,
                    'text':page,'offset':offset,'next_offset':next_offset,'done':next_offset>=len(text),
                    'source_truncated':source_truncated,'content_complete':False,'modified':item.get('modified'),'pagination_consistency':'live_note_may_change',
                    'data_mutated':False}

        @mcp.tool(annotations=annotations)
        @guard
        def notes_get(folder_id: str, id: str, representation: Literal['native','markdown']='native') -> dict:
            """Read scoped unlocked Notes HTML/text; optional Markdown is a bounded lossy view with an explicit loss report, never a native backup. No assets are fetched."""
            return NativeReader('notes').read('get', folder_id=folder_id, id=id, representation=representation)
