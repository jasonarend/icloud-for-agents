"""Public EventKit adapter, used only through exact source-bound native change plans."""
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import SafetyError, digest
from .native_access import require_write
from .native_once import planning_access
from .native_read import NativeReader, native_config
from .native_protocol import mutation_request


def validate_date(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'year','month','day','hour','minute','timezone'}:
        raise SafetyError('Use explicit Gregorian date components')
    for key in ('year','month','day'):
        if type(value.get(key)) is not int:raise SafetyError('Date requires integer year/month/day')
    if ('hour' in value)!=('minute' in value):raise SafetyError('Time requires both hour and minute')
    try:
        date(value['year'],value['month'],value['day'])
        if 'hour' in value:
            if type(value['hour']) is not int or type(value['minute']) is not int:raise ValueError()
            zone=ZoneInfo(value['timezone'])
            local=datetime(value['year'],value['month'],value['day'],value['hour'],value['minute'],tzinfo=zone)
            # Refuse nonexistent or ambiguous wall times rather than choose silently.
            if local.astimezone(ZoneInfo('UTC')).astimezone(zone).replace(tzinfo=None)!=local.replace(tzinfo=None):raise ValueError()
            if local.replace(fold=0).utcoffset()!=local.replace(fold=1).utcoffset():raise ValueError()
        elif 'timezone' in value:raise ValueError()
    except (ValueError,KeyError,TypeError,ZoneInfoNotFoundError):
        raise SafetyError('Invalid or ambiguous date/time; date-only has no timezone, timed values require one') from None
    return dict(value)


def date_parts_equal(actual,expected):
    if not isinstance(actual,dict):return False
    fixed_zero={'UTC','GMT','Etc/UTC','Etc/GMT'}
    return all((key=='timezone' and value in fixed_zero and actual.get(key) in fixed_zero)
               or actual.get(key)==value for key,value in expected.items())


def validate_link(value):
    if value is None:return None
    from urllib.parse import urlsplit
    if (not isinstance(value,str) or not 1<=len(value)<=4000 or not value.isascii()
            or any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value):
        raise SafetyError('Use a bounded ASCII URL with percent-encoded text and no whitespace')
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'http','https','applenotes','x-apple-reminderkit'} or not value.startswith(parsed.scheme+':') or parsed.username or parsed.password:raise ValueError()
        if parsed.scheme in {'http','https'} and (not parsed.hostname or parsed.port==0):raise ValueError()
        if parsed.scheme not in {'http','https'} and not (parsed.path or parsed.netloc):raise ValueError()
        # Foundation must retain this exact representation rather than repair it.
        import re
        if re.search(r'%(?![0-9a-fA-F]{2})',value) or re.search(r"[^A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]",value):raise ValueError()
    except ValueError:raise SafetyError('Use a valid HTTP(S) or supported native Notes/Reminders link without credentials') from None
    return value


def validate_alarms(values,due=None):
    if not isinstance(values,list) or len(values)>10:raise SafetyError('Use an explicit complete alarm array of at most 10 entries')
    result=[];seen=set()
    for value in values:
        if not isinstance(value,dict) or len(value)!=1:raise SafetyError('Each alarm requires one explicit time or location trigger')
        key=next(iter(value));number=value[key]
        if key=='location':
            import math
            if not isinstance(number,dict) or set(number)!={'title','latitude','longitude','radius_meters','proximity'}:raise SafetyError('Location alarms require an exact title, coordinate, radius and entry/exit trigger')
            title=number['title'];lat=number['latitude'];lon=number['longitude'];radius=number['radius_meters'];proximity=number['proximity']
            try:
                numeric=all(type(x) in (int,float) and math.isfinite(x) for x in (lat,lon,radius))
            except OverflowError:numeric=False
            if (not isinstance(title,str) or not title.strip() or len(title)>200 or any(ord(c)<32 for c in title)
                    or not numeric
                    or not -90<=lat<=90 or not -180<=lon<=180 or not 0<=radius<=100000
                    or proximity not in ('enter','leave')):raise SafetyError('Invalid location alarm; use finite coordinates, radius 0..100000 meters and enter/leave')
            canonical={'title':title,'latitude':float(lat),'longitude':float(lon),'radius_meters':float(radius),'proximity':proximity}
            identity=(key,title,float(lat),float(lon),float(radius),proximity)
            if identity in seen:raise SafetyError('Duplicate alarms are ambiguous')
            seen.add(identity);result.append({'location':canonical});continue
        if key not in {'absolute_date','relative_offset_seconds'} or type(number) is not int:raise SafetyError('Use integer Unix seconds or relative seconds, never an inferred date or trigger')
        if key=='absolute_date':
            if not 946684800<=number<=4102444799:raise SafetyError('Absolute alarm must be within 2000 through 2099')
        elif not -31536000<=number<=31536000 or not isinstance(due,dict) or 'hour' not in due or not due.get('timezone'):
            raise SafetyError('Relative alarm requires a timed due date with a timezone and an offset within one year')
        identity=(key,number)
        if identity in seen:raise SafetyError('Duplicate time alarms are ambiguous')
        seen.add(identity);result.append(dict(value))
    return result


def public_time_alarms(values):
    if not isinstance(values,list) or len(values)>10:raise SafetyError('Time-alarm metadata is incomplete')
    result=[]
    for value in values:
        required={'type','proximity','structured_location','email_address','sound_name','absolute_date','relative_offset_seconds'}
        if (not isinstance(value,dict) or not required<=set(value) or type(value.get('type')) is not int or value.get('type')!=0 or type(value.get('proximity')) is not int or value.get('proximity')!=0
                or any(value.get(key) is not None for key in ('structured_location','email_address','sound_name'))):
            raise SafetyError('Location, email, procedure, custom-sound and unknown alarms require a separate fidelity-capable route')
        absolute=value.get('absolute_date');key='absolute_date' if absolute is not None else 'relative_offset_seconds';number=value.get(key)
        import math
        if type(number) not in (int,float) or not math.isfinite(number) or int(number)!=number:raise SafetyError('Time-alarm precision is not representable by this route')
        result.append({key:int(number)})
    return result


def public_supported_alarms(values):
    if not isinstance(values,list) or len(values)>10:raise SafetyError('Complete supported alarm metadata required')
    observed=[]
    for value in values:
        if not isinstance(value,dict) or not {'type','proximity','structured_location','email_address','sound_name','absolute_date','relative_offset_seconds'}<=set(value):raise SafetyError('Invalid or incomplete alarm metadata')
        if value.get('structured_location') is None:observed.extend(public_time_alarms([value]));continue
        if (type(value.get('type')) is not int or value['type']!=0 or type(value.get('proximity')) is not int or value['proximity'] not in (1,2) or value.get('absolute_date') is not None
                or value.get('relative_offset_seconds')!=0 or value.get('email_address') is not None or value.get('sound_name') is not None):raise SafetyError('Unsupported location alarm metadata')
        place=value['structured_location']
        if not isinstance(place,dict) or set(place)!={'title','latitude','longitude','radius_meters'}:raise SafetyError('Incomplete location alarm metadata')
        observed.extend(validate_alarms([{'location':{**place,'proximity':'enter' if value['proximity']==1 else 'leave'}}]))
    # Duplicate existing locations are not safely addressable as a set.
    keys=[json.dumps(x,sort_keys=True) for x in observed]
    if len(set(keys))!=len(keys):raise SafetyError('Duplicate exposed alarms are ambiguous')
    return observed


def alarms_equal(actual,expected):
    try:
        observed=public_supported_alarms(actual)
        import json
        # Provider ordering does not define notification priority. Exact coordinate
        # and radius readback is required; no silent rounding or location substitution.
        return sorted(json.dumps(x,sort_keys=True) for x in observed)==sorted(json.dumps(x,sort_keys=True) for x in expected)
    except (SafetyError,TypeError,ValueError,OverflowError):return False


def alarm_effects(alarms,due):
    import time
    triggers=[]
    locations=[]
    for alarm in alarms:
        if 'location' in alarm:
            locations.append(dict(alarm['location']));continue
        if 'absolute_date' in alarm:seconds=alarm['absolute_date']
        else:
            moment=datetime(due['year'],due['month'],due['day'],due['hour'],due['minute'],tzinfo=ZoneInfo(due['timezone']))
            seconds=moment.timestamp()+alarm['relative_offset_seconds']
        triggers.append({'unix_seconds':seconds,'utc':datetime.fromtimestamp(seconds,timezone.utc).isoformat()})
    return {'action':'replace the complete supported time/location alarm set; an empty array cancels those alarms',
            'triggers':triggers,'past_trigger_may_notify_immediately':any(x['unix_seconds']<=time.time() for x in triggers),
            'location_triggers':locations,'location_delivery':'Apple controls location permissions and notification delivery; no location service is started by the helper',
            'devices':'Apple may sync scheduling changes across devices; delivery is unverified',
            'provider_limits':'Exact count and values must survive readback; provider truncation is a failure'}


class RemindersCreateAdapter:
    operations={'create_list','create_reminder','update_reminder','set_completed','move_reminder','rename_list','set_recurrence','set_list_color'}
    def __init__(self):self.reader=NativeReader('reminders')
    @property
    def backend_id(self):
        helper=native_config('reminders').get('helper_path')
        if not helper:raise SafetyError('Configure native helper before Reminders writes')
        path=Path(helper)
        if not path.is_file() or path.stat().st_size>50_000_000:raise SafetyError('Invalid native helper')
        identity='eventkit-creation-v1:'+hashlib.sha256(path.read_bytes()).hexdigest()
        if native_config('reminders').get('advanced_backend')=='reminderkit':identity+=':reminderkit-observed-metadata-preservation-v2'
        return identity
    def lists(self):
        result=[];offset=0
        while True:
            page=self.reader.read('lists',limit=100,offset=offset);result.extend(page['lists'])
            if page.get('done'):return result
            next_offset=page.get('next_offset')
            if type(next_offset) is not int or next_offset<=offset or next_offset>10000:
                raise SafetyError('List discovery incomplete; no write')
            offset=next_offset
    def list_rename_snapshot(self,item):
        lists=[c for c in self.lists() if c['source_id']==item['source_id']]
        matches=[c for c in lists if c['id']==item['list_id']]
        if len(matches)!=1 or matches[0].get('writable') is not True:raise SafetyError('Select one writable reminder list')
        siblings=sorted((c for c in lists if c['id']!=item['list_id']),key=lambda c:c['id'])
        if 'title' in item['fields'] and any(c['title']==item['fields']['title'] for c in siblings):raise SafetyError('Reminder list title already exists in this source')
        membership=self.reader.read('list_membership',list_id=item['list_id'])
        if (membership.get('list_id')!=item['list_id'] or membership.get('source_id')!=item['source_id']
                or type(membership.get('count')) is not int or not 0<=membership['count']<=100000
                or not isinstance(membership.get('sha256'),str) or len(membership['sha256'])!=64
                or any(c not in '0123456789abcdef' for c in membership['sha256'])):raise SafetyError('Reminder list membership is not fully verified')
        return {'list':matches[0],'siblings':siblings,'membership':{k:membership[k] for k in ('list_id','source_id','count','sha256')}}
    def prepare_list_rename(self,change):
        if set(change)!={'operation','source_id','list_id','fields'}:raise SafetyError('List renaming requires one exact list and title')
        source=change['source_id'];fields=change['fields'];identity=change['list_id']
        if source not in planning_access('reminders')['source_ids'] or not isinstance(identity,str) or not 1<=len(identity)<=1000:
            raise SafetyError('Select an exact source and reminder list')
        if not isinstance(fields,dict) or set(fields)!={'title'}:raise SafetyError('Only the list title can change')
        title=fields['title']
        if not isinstance(title,str) or not title.strip() or len(title)>1000 or any(ord(c)<32 for c in title):raise SafetyError('Invalid bounded list title')
        item={'operation':'rename_list','source_id':source,'list_id':identity,'id':identity,'fields':dict(fields),'fidelity_verified':True,
              'effects':{'list':'Rename this exact list; no task move, completion or deletion.',
                         'sharing':'Title may sync to devices or existing participants; EventKit does not expose participant state.'}}
        item['before']=self.list_rename_snapshot(item)
        if item['before']['list']['title']==title:raise SafetyError('List already has the requested title')
        return item

    def prepare_list_color(self,change,*,allow_noop=False):
        from .native_colors import validate_color,colors_equal
        if not isinstance(change,dict) or set(change)!={'operation','source_id','list_id','fields'} or change.get('operation')!='set_list_color':raise SafetyError('Select an exact source and list for its color')
        if change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['list_id'],str) or not 1<=len(change['list_id'])<=1000:raise SafetyError('Select one exact writable list')
        if not isinstance(change['fields'],dict) or set(change['fields'])!={'color'}:raise SafetyError('List color changes only its explicit sRGB color')
        color=validate_color(change['fields']['color'])
        item={'operation':'set_list_color','source_id':change['source_id'],'list_id':change['list_id'],'id':change['list_id'],'fields':{'color':color},'fidelity_verified':True,
              'effects':{'list':'Change only the exact native list color; preserve its title, identity and complete task membership',
                         'color_space':'sRGB RGBA; independent readback tolerance is one millionth per channel',
                         'sync':'Apple controls device sync and participant visibility; native icons and pins are unchanged'}}
        item['before']=self.list_rename_snapshot(item)
        validate_color(item['before']['list'].get('color'))
        if not allow_noop and colors_equal(item['before']['list']['color'],color):raise SafetyError('List already has the requested color')
        return item

    def prepare_recurrence(self,change):
        from .native_recurrence import validate_recurrence,observed_rule,rule_effects,validate_seed
        if set(change)!={'operation','source_id','id','fields'} or not isinstance(change.get('id'),str) or not 1<=len(change['id'])<=1000:
            raise SafetyError('Select one exact reminder for its native recurrence rule')
        if change['source_id'] not in planning_access('reminders')['source_ids']:raise SafetyError('Select an exact source')
        fields=change['fields']
        if not isinstance(fields,dict) or set(fields)!={'recurrence','recurrence_scope'} or fields['recurrence_scope']!='exact_native_rule':
            raise SafetyError('Rule changes require recurrence and recurrence_scope exact_native_rule; no occurrence or implicit series edits')
        rule=validate_recurrence(fields['recurrence'])
        item={'operation':'set_recurrence','source_id':change['source_id'],'id':change['id'],'fields':{'recurrence':rule,'recurrence_scope':'exact_native_rule'},'fidelity_verified':True,'effects':rule_effects(rule)}
        item['before']=self.snapshot(item);current=item['before']['item']
        if current.get('completed') is not False or current.get('public_metadata_complete') is not True:
            raise SafetyError('Recurrence requires an uncompleted reminder with complete public metadata')
        rules=current.get('recurrence_rules')
        if not isinstance(rules,list) or len(rules)>1 or bool(rules)!=current.get('has_recurrence'):raise SafetyError('Unsupported or incomplete native recurrence set')
        if rules:observed_rule(rules[0])
        validate_seed(rule,current.get('due'))
        return item

    def prepare_existing(self,change):
        allowed={'operation','source_id','id','fields'}
        if set(change)-allowed or not isinstance(change.get('id'),str) or not change['id']:
            raise SafetyError('Existing reminder changes require one exact ID')
        operation=change['operation'];source=change.get('source_id');fields=change.get('fields')
        if source not in planning_access('reminders')['source_ids'] or not isinstance(fields,dict) or not fields:
            raise SafetyError('Select an exact source and nonempty fields')
        if operation=='set_completed':
            if set(fields)!={'completed'} or type(fields['completed']) is not bool:
                raise SafetyError('Use an explicit completion boolean')
        elif operation=='move_reminder':
            if set(fields)!={'list_id'} or not isinstance(fields['list_id'],str) or not fields['list_id']:
                raise SafetyError('Select an exact destination list in the same source')
        else:
            if set(fields)-{'title','notes','priority','due','start','url','alarms'}:
                raise SafetyError('Unsupported reminder update fields')
            # Reuse creation validation without injecting defaults into the patch.
            current=self.reader.read('get',id=change['id'])['item']
            if 'alarms' in fields and current.get('has_alarms'):
                if current.get('public_metadata_complete') is not True:raise SafetyError('Complete existing alarm metadata required before replacement')
                public_supported_alarms(current.get('alarms'))
            merged={key:current[key] for key in ('title','notes','priority')}
            for key in ('due','start'):
                value=current.get(key)
                merged[key]=({k:v for k,v in value.items() if k in {'year','month','day','hour','minute','timezone'} and v is not None}
                             if isinstance(value,dict) else None)
            for key in ('url',):
                if key in current:merged[key]=current[key]
            validated=self.prepare({'operation':'create_reminder','source_id':source,
                'list_id':current['list_id'],'fields':{**merged,**fields}})['fields']
            fields={key:validated[key] for key in fields}
        item={'operation':operation,'source_id':source,'id':change['id'],'fields':dict(fields),
              'fidelity_verified':True,'effects':{'existing_content':'only explicitly listed public EventKit fields change',
              'native_metadata':'advanced native-field preservation requires live acceptance',
              'notifications':'Apple controls sync and notification delivery'}}
        if native_config('reminders').get('advanced_backend')=='reminderkit':item['effects']['native_metadata']='Observed flags, native tags and partial hierarchy metadata must be unchanged before dispatch and during independent readback; complete subtree and other native metadata remain unverified.'
        if 'alarms' in item['fields']:item['effects']['notifications']='Replace the complete supported time/location alarm set; may schedule, reschedule or cancel notifications on synced devices. Past triggers may notify immediately; delivery remains controlled by Apple.'
        item['before']=self.snapshot(item)
        if 'alarms' in item['fields']:
            due=item['fields'].get('due',item['before']['item'].get('due'))
            item['effects']['time_alarms']=alarm_effects(item['fields']['alarms'],due)
        if item['before']['item'].get('has_recurrence'):
            raise SafetyError('Recurring reminder edits require explicit series/occurrence semantics')
        return item

    def prepare(self,change):
        if isinstance(change,dict) and change.get('operation')=='set_list_color':return self.prepare_list_color(change)
        if isinstance(change,dict) and change.get('operation')=='set_recurrence':return self.prepare_recurrence(change)
        if isinstance(change,dict) and change.get('operation')=='rename_list':return self.prepare_list_rename(change)
        if isinstance(change,dict) and change.get('operation') in {'update_reminder','set_completed','move_reminder','set_recurrence'}:
            return self.prepare_existing(change)
        if not isinstance(change,dict) or set(change)-{'operation','source_id','list_id','fields'}:
            raise SafetyError('Unknown Reminders change fields')
        source=change.get('source_id');operation=change.get('operation');fields=change.get('fields')
        if operation not in self.operations or source not in planning_access('reminders')['source_ids']:
            raise SafetyError('Unselected Reminders source or operation')
        allowed={'title'} if operation=='create_list' else {'title','notes','priority','due','start','url','alarms','recurrence'}
        if not isinstance(fields,dict) or set(fields)-allowed:raise SafetyError('Unsupported reminder fields')
        title=fields.get('title')
        if not isinstance(title,str) or not title.strip() or len(title)>1000 or any(ord(c)<32 for c in title):
            raise SafetyError('Invalid bounded reminder title')
        fields=dict(fields)
        if operation=='create_reminder':
            notes=fields.get('notes','');priority=fields.get('priority',0)
            if not isinstance(notes,str) or len(notes.encode())>20000 or '\x00' in notes:raise SafetyError('Invalid reminder notes')
            if type(priority) is not int or priority not in {0,1,5,9}:raise SafetyError('Use none/high/medium/low priority values 0/1/5/9')
            fields.update(notes=notes,priority=priority)
            if 'url' in fields:fields['url']=validate_link(fields['url'])
            if 'alarms' in fields:fields['alarms']=validate_alarms(fields['alarms'],fields.get('due'))
            for key in ('due','start'):
                if key in fields:fields[key]=validate_date(fields[key])
        if operation=='create_reminder' and 'recurrence' in fields:
            from .native_recurrence import validate_recurrence,validate_seed
            fields['recurrence']=validate_recurrence(fields['recurrence'])
            validate_seed(fields['recurrence'],fields.get('due'))
        if fields.get('start') and fields.get('due'):
            start,due=fields['start'],fields['due']
            if ('hour' in start)!=('hour' in due):raise SafetyError('Mixed date-only/timed start and due require an explicit policy')
            def instant(d):
                if 'hour' not in d:return date(d['year'],d['month'],d['day'])
                return datetime(d['year'],d['month'],d['day'],d['hour'],d['minute'],tzinfo=ZoneInfo(d['timezone']))
            if instant(start)>instant(due):raise SafetyError('Reminder start must not follow its due date')
        item={'operation':operation,'source_id':source,'fields':fields,'fidelity_verified':True,
              'effects':{'existing_content':'unchanged','sharing':'EventKit does not expose participant state; verify this list is private before enabling writes','notifications':'native due-date behavior only; delivery unverified'}}
        if operation=='create_reminder':
            if not isinstance(change.get('list_id'),str) or not change['list_id']:raise SafetyError('Select an exact reminder list')
            item['list_id']=change['list_id']
        item['before']=self.snapshot(item)
        if operation=='create_reminder' and 'alarms' in fields:item['effects']['time_alarms']=alarm_effects(fields['alarms'],fields.get('due'))
        if operation=='create_reminder' and 'recurrence' in fields:
            from .native_recurrence import rule_effects
            item['effects']['recurrence']=rule_effects(fields['recurrence'])
        if operation=='create_list' and item['before']['matching_titles']>0:raise SafetyError('List title already exists')
        return item
    def native_snapshot(self,public_item):
        if native_config('reminders').get('advanced_backend')!='reminderkit':return None
        observed=dict(self.reader.read('get',id=public_item['id'],include_native_metadata=True,include_native_tags=True,include_native_hierarchy=True,include_native_children=True)['item'])
        metadata=observed.pop('native_metadata',None)
        if (observed!=public_item or not isinstance(metadata,dict) or metadata.get('coverage')!=['flagged','tags','hierarchy','children']
                or type(metadata.get('flagged')) is not bool or metadata.get('tags_complete') is not True
                or not isinstance(metadata.get('tags'),list) or type(metadata.get('is_subtask')) is not bool
                or (metadata.get('has_subtasks') is not None and type(metadata['has_subtasks']) is not bool)
                or (metadata.get('child_ids') is not None and (not isinstance(metadata['child_ids'],list) or any(not isinstance(x,str) for x in metadata['child_ids'])))
                or metadata.get('hierarchy_complete') is not False or metadata.get('children_complete') is not False
                or (metadata.get('is_subtask') and not isinstance(metadata.get('parent_id'),str))
                or (not metadata.get('is_subtask') and metadata.get('parent_id') is not None)
                or (metadata.get('children_status')=='unavailable') != (metadata.get('has_subtasks') is None)
                or metadata.get('children_status') not in {'unavailable','provider_reported'}
                or (metadata.get('children_fetch_status')=='unavailable') != (metadata.get('child_ids') is None)
                or metadata.get('children_fetch_status') not in {'unavailable','provider_reported'}
                or (metadata.get('has_subtasks') is False and bool(metadata.get('child_ids')))
                or any(metadata.get(key)!=public_item.get(key) for key in ('id','source_id','list_id'))):
            raise SafetyError('Exact optional native metadata snapshot is unavailable or changed')
        return metadata

    def snapshot(self,item):
        # No current backend proves a complete subtree before a cross-list move.
        # Apply this to fresh plans and rechecks of already approved plans.
        if item['operation']=='move_reminder':
            raise SafetyError('Reminder moves are unavailable until complete hierarchy effects can be verified before dispatch')
        if item['operation'] in {'rename_list','set_list_color'}:return self.list_rename_snapshot(item)
        lists=[c for c in self.lists() if c['source_id']==item['source_id']]
        if item['operation'] in {'update_reminder','set_completed','move_reminder','set_recurrence'}:
            reminder=self.reader.read('get',id=item['id'])['item']
            if reminder.get('source_id')!=item['source_id']:raise SafetyError('Reminder belongs to another source')
            current=next((c for c in lists if c['id']==reminder['list_id']),None)
            if not current or not current.get('writable'):raise SafetyError('Reminder list is not writable')
            result={'list':current,'item':reminder}
            native=self.native_snapshot(reminder)
            if native is not None:result['native_metadata']=native
            if item['operation']=='move_reminder':
                destination=next((c for c in lists if c['id']==item['fields']['list_id']),None)
                if not destination or not destination.get('writable'):raise SafetyError('Destination is absent, out of source scope or read-only')
                result['destination']=destination
            return result
        if item['operation']=='create_list':
            return {'source_id':item['source_id'],'membership_sha256':digest(sorted(lists,key=lambda c:c['id'])),
                    'list_count':len(lists),'matching_titles':sum(c['title']==item['fields']['title'] for c in lists)}
        matches=[c for c in lists if c['id']==item['list_id']]
        if len(matches)!=1 or not matches[0].get('writable'):raise SafetyError('Reminder list is absent, unselected or read-only')
        return matches[0]
    def apply(self,item):
        require_write('reminders',item);config=native_config('reminders')
        request=mutation_request('reminders',item)
        return self.reader._dispatch(request,config)['result']
    def verify(self,item,outcome):
        if outcome.get('source_id')!=item['source_id'] or not isinstance(outcome.get('id'),str):return False
        if item['operation']=='set_list_color':
            from .native_colors import colors_equal
            if outcome['id']!=item['list_id']:return False
            actual=self.list_rename_snapshot(item);expected=item['before']
            return (colors_equal(actual['list'].get('color'),item['fields']['color']) and
                    {k:v for k,v in actual['list'].items() if k!='color'}=={k:v for k,v in expected['list'].items() if k!='color'} and
                    actual['siblings']==expected['siblings'] and actual['membership']==expected['membership'])
        if item['operation']=='rename_list':
            if outcome['id']!=item['list_id']:return False
            actual=self.list_rename_snapshot(item);expected=dict(item['before']);expected['list']=dict(expected['list'],title=item['fields']['title'])
            return actual==expected
        if item['operation']=='create_list':return any(c['id']==outcome['id'] and c['title']==item['fields']['title'] and c['source_id']==item['source_id'] for c in self.lists())
        note=self.reader.read('get',id=outcome['id'])['item']
        if item['operation'] in {'update_reminder','set_completed','move_reminder','set_recurrence'}:
            if outcome['id']!=item['id'] or note.get('source_id')!=item['source_id']:return False
            expected=dict(item['before']['item']);expected.update(item['fields'])
            if 'alarms' in item['fields']:expected['has_alarms']=bool(item['fields']['alarms'])
            if item['operation']=='set_recurrence':
                from .native_recurrence import recurrence_equal
                if not recurrence_equal(note.get('recurrence_rules'),item['fields']['recurrence']) or note.get('has_recurrence')!=bool(item['fields']['recurrence']):return False
                if note.get('created')!=expected.get('created') or note.get('public_metadata_complete') is not True:return False
                expected['recurrence_rules']=note['recurrence_rules'];expected['has_recurrence']=note['has_recurrence']
            native=self.native_snapshot(note);expected_native=item['before'].get('native_metadata')
            if expected_native is not None:
                expected_native=dict(expected_native,list_id=expected['list_id'])
            if native!=expected_native:return False
            for key in ('title','notes','priority','due','start','list_id','completed','url','location','has_recurrence','has_alarms','recurrence_rules','alarms'):
                if key not in expected:continue
                actual=note.get(key);wanted=expected.get(key)
                if key=='alarms' and 'alarms' in item['fields']:
                    if not alarms_equal(actual,wanted):return False
                elif key in {'due','start'} and isinstance(wanted,dict):
                    if not date_parts_equal(actual,wanted):return False
                elif actual!=wanted:return False
            return True
        if note.get('source_id')!=item['source_id'] or note.get('list_id')!=item['list_id'] or note.get('completed') is not False:return False
        if any(note.get(k)!=item['fields'][k] for k in ('title','notes','priority')):return False
        if 'recurrence' not in item['fields'] and (note.get('has_recurrence',False) or note.get('recurrence_rules',[])):return False
        if 'recurrence' in item['fields']:
            from .native_recurrence import recurrence_equal
            if not recurrence_equal(note.get('recurrence_rules'),item['fields']['recurrence']) or note.get('has_recurrence')!=bool(item['fields']['recurrence']):return False
        if 'alarms' in item['fields'] and (not alarms_equal(note.get('alarms'),item['fields']['alarms']) or note.get('has_alarms')!=bool(item['fields']['alarms'])):return False
        for key in ('url','location'):
            if note.get(key)!=item['fields'].get(key):return False
        for key in ('due','start'):
            expected=item['fields'].get(key);actual=note.get(key)
            if expected is None:
                if actual is not None:return False
            elif not date_parts_equal(actual,expected):return False
        return True
