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
    operations={'set_smart_list_sources','rename_smart_list','reparent_reminder','reorder_group','group_list','ungroup_list','rename_group','create_group','reorder_reminder','set_list_badge','delete_reminder','restore_reminder','set_list_pinned','create_list','create_reminder','create_section','rename_section','update_reminder','set_completed','move_reminder','rename_list','set_recurrence','set_list_color','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}
    def __init__(self):self.reader=NativeReader('reminders')
    def bind_owner_context(self,account,store):self._plan_owner=account;self._plan_store=store
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

    def section_snapshot(self,item):
        base=self.list_rename_snapshot(dict(item,fields={}))
        sections=self.reader.read('list_sections',list_id=item['list_id'])
        if sections.get('source_id')!=item['source_id'] or sections.get('list_id')!=item['list_id']:
            raise SafetyError('Section inventory belongs to another source or list')
        features=self.reader.read('list_features',list_id=item['list_id'])
        if (features.get('source_id')!=item['source_id'] or features.get('list_id')!=item['list_id'] or features.get('grocery_categorization') is not False
                or features.get('grocery_shopping_session') is not False or features.get('is_group') is not False):
            raise SafetyError('Section writes require the grocery-categorization, shopping-session and group flags to be false')
        base['list_features']=features
        base['sections']=sections['sections']
        return base

    def prepare_section(self,change):
        import uuid
        if set(change)!={'operation','source_id','list_id','fields'} or change.get('operation')!='create_section':raise SafetyError('Select one exact list and section title')
        if change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['list_id'],str) or not 1<=len(change['list_id'])<=1000:raise SafetyError('Select an exact source and list')
        if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Section creation requires the explicitly selected ReminderKit backend')
        fields=change['fields'];title=fields.get('title') if isinstance(fields,dict) else None
        if not isinstance(fields,dict) or set(fields)!={'title'} or not isinstance(title,str) or not title.strip() or len(title)>1000 or len(title.encode('utf-8'))>4000 or any(ord(c)<32 for c in title):raise SafetyError('Use one exact bounded section title')
        item={'operation':'create_section','source_id':change['source_id'],'list_id':change['list_id'],'id':change['list_id'],'section_id':str(uuid.uuid4()).upper(),'fields':{'title':title},'fidelity_verified':True,
              'effects':{'section':'Create one native section; preserve the observed existing sections and list reminder membership.','ordering':'Apple determines display order; section ordering and reminder membership within sections are not verified.','list_type':'Provider flags do not establish the complete list type; section or category behavior may differ on special lists.','sharing':'Apple may sync the list change to devices or existing participants; EventKit does not expose participant state.'}}
        item['before']=self.section_snapshot(item)
        item['effects']['observed_sharing']={key:item['before']['list_features'][key] for key in ('shared','shared_to_me','sharing_metadata_complete')}
        if len(item['before']['sections'])>=100 or any(s['title']==title for s in item['before']['sections']):raise SafetyError('A matching section exists or section inventory reached its bound')
        return item

    def prepare_section_rename(self,change):
        import uuid
        if set(change)!={'operation','source_id','list_id','section_id','fields'} or change.get('operation')!='rename_section':raise SafetyError('Select one exact source, list and section')
        if change['source_id'] not in planning_access('reminders')['source_ids'] or not isinstance(change['list_id'],str) or not 1<=len(change['list_id'])<=1000:raise SafetyError('Select one exact source and list')
        try:
            section=change['section_id']
            if not isinstance(section,str) or str(uuid.UUID(section)).upper()!=section.upper():raise ValueError()
        except ValueError:raise SafetyError('Select one exact native section ID') from None
        section=str(uuid.UUID(section)).upper()
        if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Section renaming requires the explicitly selected ReminderKit backend')
        fields=change['fields'];title=fields.get('title') if isinstance(fields,dict) else None
        if not isinstance(fields,dict) or set(fields)!={'title'} or not isinstance(title,str) or not title.strip() or len(title)>1000 or len(title.encode('utf-8'))>4000 or any(ord(c)<32 for c in title):raise SafetyError('Use one exact bounded section title')
        item={'operation':'rename_section','source_id':change['source_id'],'list_id':change['list_id'],'id':change['list_id'],'section_id':section,'fields':{'title':title},'fidelity_verified':True,
              'effects':{'section':'Rename only the selected native section; preserve observed sections and list reminder membership.','ordering':'Apple controls display order; section membership and ordering remain unverified.','list_type':'Provider flags do not establish the complete list type; section or category behavior may differ on special lists.','sharing':'Apple may sync the list change to devices or existing participants; EventKit does not expose participant state.'}}
        item['before']=self.section_snapshot(item)
        item['effects']['observed_sharing']={key:item['before']['list_features'][key] for key in ('shared','shared_to_me','sharing_metadata_complete')}
        matches=[s for s in item['before']['sections'] if s['id'].upper()==section.upper()]
        if len(matches)!=1 or matches[0]['title']==title or any(s['title']==title for s in item['before']['sections']):raise SafetyError('Section is absent, unchanged or duplicates an existing title')
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
        if operation=='create_subtask':
            title=fields.get('title')
            if set(fields)!={'title'} or not isinstance(title,str) or not title.strip() or len(title)>1000 or len(title.encode('utf-8'))>4000 or any(ord(c)<32 for c in title):raise SafetyError('Use one exact bounded subtask title')
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native subtask creation requires the explicitly selected ReminderKit backend')
        elif operation=='set_completed':
            if set(fields)!={'completed'} or type(fields['completed']) is not bool:
                raise SafetyError('Use an explicit completion boolean')
        elif operation in {'add_tag','replace_tag'}:
            import unicodedata,uuid
            name=fields.get('tag_name')
            if (set(fields)!=({'tag_name','tag_id'} if operation=='replace_tag' else {'tag_name'}) or not isinstance(name,str) or not 1<=len(name)<=100
                    or len(name.encode('utf-8','surrogatepass'))>512
                    or any(not (c=='_' or c.isalnum() or unicodedata.category(c).startswith('M')) for c in name)
                    or unicodedata.category(name[0]).startswith('M')):
                raise SafetyError('Use one native tag name of 1 to 100 letters, digits or underscores without #')
            if operation=='replace_tag':
                try:
                    if not isinstance(fields['tag_id'],str) or str(uuid.UUID(fields['tag_id'])).upper()!=fields['tag_id'].upper():raise ValueError()
                except ValueError:raise SafetyError('Use one exact native tag ID') from None
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native tag changes require the explicitly selected ReminderKit backend')
        elif operation=='remove_tag':
            import uuid
            if set(fields)!={'tag_id'} or not isinstance(fields['tag_id'],str):raise SafetyError('Use one exact native tag ID')
            try:
                if str(uuid.UUID(fields['tag_id'])).upper()!=fields['tag_id'].upper():raise ValueError()
            except ValueError:raise SafetyError('Use one exact native tag ID') from None
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native tag changes require the explicitly selected ReminderKit backend')
        elif operation=='clear_early':
            if set(fields)!={'clear'} or fields.get('clear') is not True:raise SafetyError('Explicitly request clear=true for the single reviewed early reminder')
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native early reminders require the explicitly selected ReminderKit backend')
        elif operation=='add_early':
            if (set(fields)!={'unit','count'} or not isinstance(fields.get('unit'),str) or fields['unit'] not in {'minutes','hours','days','weeks','months'} or type(fields.get('count')) is not int or not 1<=fields['count']<=10000):
                raise SafetyError('Choose minutes, hours, days, weeks or months and a positive whole number before the due date')
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native early reminders require the explicitly selected ReminderKit backend')
        elif operation=='set_urgent':
            if set(fields)!={'urgent'} or type(fields['urgent']) is not bool:raise SafetyError('Use an explicit native urgent Boolean')
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native urgent writes require the explicitly selected ReminderKit backend')
        elif operation=='set_flagged':
            if set(fields)!={'flagged'} or type(fields['flagged']) is not bool:raise SafetyError('Use an explicit native flag Boolean')
            if native_config('reminders').get('advanced_backend')!='reminderkit':raise SafetyError('Native flag writes require the explicitly selected ReminderKit backend')
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
        if native_config('reminders').get('advanced_backend')=='reminderkit':item['effects']['native_metadata']='Observed flags, native tags, urgent state, early reminders, section relationship and partial hierarchy metadata must be unchanged before dispatch and during independent readback. An unreported section remains unknown; complete subtree and other native metadata remain unverified.'
        if operation=='set_completed':
            item['effects']['hierarchy']='Change this reminder’s completion state. Apple controls any effects on related reminders; descendant completion states are not verified by this operation.'
        if 'alarms' in item['fields']:item['effects']['notifications']='Replace the complete supported time/location alarm set; may schedule, reschedule or cancel notifications on synced devices. Past triggers may notify immediately; delivery remains controlled by Apple.'
        if operation=='create_subtask':
            import uuid
            item['child_id']=str(uuid.uuid4()).upper()
            item['effects']['hierarchy']='Create one native subtask under this exact parent; existing observed child IDs and other metadata must be retained. Provider hierarchy remains incomplete.'
        item['before']=self.snapshot(item)
        if operation=='create_subtask':
            metadata=item['before'].get('native_metadata');children=metadata.get('child_ids') if isinstance(metadata,dict) else None
            if not isinstance(metadata,dict) or metadata.get('is_subtask') is not False:
                raise SafetyError('Creating beneath an existing subtask has not passed provider save validation; select a root reminder')
            if item['before']['item'].get('completed') is not False or not isinstance(children,list) or len(children)>=100 or item['child_id'] in children:raise SafetyError('Subtask creation requires an incomplete parent and a bounded observed child list')
        if operation=='update_reminder' and 'due' in fields:
            metadata=item['before'].get('native_metadata');early=metadata.get('early_alerts') if isinstance(metadata,dict) else None
            if early:
                due=fields['due']
                if not isinstance(due,dict) or due.get('hour') is None or due.get('minute') is None:raise SafetyError('Clear the native early reminder before removing its timed due date')
                item['effects']['early_reminder']='Changing the due date also reschedules the observed native early reminder; Apple controls notification delivery.'
            elif metadata is None:
                item['effects']['early_reminder']='Native early-reminder state is unavailable on this backend; changing the due date may reschedule or remove an early notification.'
        if operation=='clear_early':
            metadata=item['before'].get('native_metadata')
            if not isinstance(metadata,dict) or not isinstance(metadata.get('early_alerts'),list) or len(metadata['early_alerts'])!=1 or not isinstance(metadata.get('early_payload_fingerprint'),str):raise SafetyError('Clearing requires exactly one observed native early alert with its complete payload fingerprint')
            item['effects']['early_reminder']='Remove the one reviewed native early reminder; retain due date and ordinary alarms. Apple controls notification cancellation and sync.'
        if operation=='add_early':
            metadata=item['before'].get('native_metadata');due=item['before']['item'].get('due')
            if not isinstance(metadata,dict) or metadata.get('early_alerts')!=[] or metadata.get('early_payload_fingerprint') is not None:raise SafetyError('Adding an early reminder requires an observed empty native early-alert state')
            if not isinstance(due,dict) or due.get('hour') is None or due.get('minute') is None:raise SafetyError('An early reminder requires an explicit timed due date')
            item['effects']['early_reminder']='Add one native early reminder before the due date; Apple controls sync and notification delivery.'
        if operation=='set_urgent':
            if item['before'].get('urgent') is fields['urgent']:raise SafetyError('Reminder already has the requested urgent state')
            item['effects']['urgent']='Change the current user urgent state. Apple controls sync and notifications; urgent notifications may repeat.'
        if operation=='set_flagged':
            metadata=item['before'].get('native_metadata')
            if not isinstance(metadata,dict) or type(metadata.get('flagged')) is not bool:raise SafetyError('Exact native flag state is required')
            if metadata['flagged'] is fields['flagged']:raise SafetyError('Reminder already has the requested flag state')
            item['effects']['native_flag']='Change the exact native flag; priority and all other observed metadata must remain unchanged.'
        if operation in {'add_tag','replace_tag'}:
            metadata=item['before'].get('native_metadata');tags=metadata.get('tags') if isinstance(metadata,dict) else None
            if not isinstance(tags,list) or (operation=='add_tag' and len(tags)>=200) or any(not isinstance(t,dict) or not isinstance(t.get('name'),str) for t in tags):raise SafetyError('Complete bounded native tag metadata is required')
            if any(t['name'].casefold()==fields['tag_name'].casefold() for t in tags):raise SafetyError('A matching tag already exists on this reminder')
            if operation=='replace_tag' and len([t for t in tags if isinstance(t.get('id'),str) and t['id'].upper()==fields['tag_id'].upper()])!=1:raise SafetyError('Select one exact existing native tag on this reminder')
            item['effects']['native_tag']=('Replace one native tag relationship with a newly identified tag; preserve all other observed tags and hierarchy. Apple controls synchronization.' if operation=='replace_tag' else 'Add one native tag relationship; preserve all other observed tags and hierarchy. Apple controls synchronization.')
        if operation=='remove_tag':
            metadata=item['before'].get('native_metadata');tags=metadata.get('tags') if isinstance(metadata,dict) else None
            if (not isinstance(tags,list) or any(not isinstance(t,dict) or not isinstance(t.get('id'),str) for t in tags)
                    or len([t for t in tags if t['id'].upper()==fields['tag_id'].upper()])!=1):raise SafetyError('Select one exact existing native tag on this reminder')
            item['effects']['native_tag']='Remove only the selected native tag relationship; retain all other observed tags and hierarchy. Apple controls synchronization.'
        if 'alarms' in item['fields']:
            due=item['fields'].get('due',item['before']['item'].get('due'))
            item['effects']['time_alarms']=alarm_effects(item['fields']['alarms'],due)
        if item['before']['item'].get('has_recurrence'):
            raise SafetyError('Recurring reminder edits require explicit series/occurrence semantics')
        return item

    def prepare(self,change):
        if isinstance(change,dict) and change.get('operation') in {'delete_reminder','restore_reminder'}:
            from .native_reminder_lifecycle import prepare_delete,prepare_restore
            return prepare_delete(self,change) if change['operation']=='delete_reminder' else prepare_restore(self,change)
        if isinstance(change,dict) and change.get('operation')=='set_smart_list_sources':
            from .native_reminder_smart_sources import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='rename_smart_list':
            from .native_reminder_smart_rename import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='reparent_reminder':
            from .native_reminder_parents import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='reorder_group':
            from .native_group_order import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation') in {'group_list','ungroup_list'}:
            from .native_list_groups import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='rename_group':
            from .native_reminder_groups import prepare_rename
            return prepare_rename(self,change)
        if isinstance(change,dict) and change.get('operation')=='create_group':
            from .native_reminder_groups import prepare_create
            return prepare_create(self,change)
        if isinstance(change,dict) and change.get('operation')=='reorder_reminder':
            from .native_reminder_order import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='set_list_badge':
            from .native_list_badges import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='set_list_pinned':
            from .native_list_pins import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='move_reminder':
            from .native_reminder_moves import prepare
            return prepare(self,change)
        if isinstance(change,dict) and change.get('operation')=='create_section':return self.prepare_section(change)
        if isinstance(change,dict) and change.get('operation')=='rename_section':return self.prepare_section_rename(change)
        if isinstance(change,dict) and change.get('operation')=='set_list_color':return self.prepare_list_color(change)
        if isinstance(change,dict) and change.get('operation')=='set_recurrence':return self.prepare_recurrence(change)
        if isinstance(change,dict) and change.get('operation')=='rename_list':return self.prepare_list_rename(change)
        if isinstance(change,dict) and change.get('operation') in {'update_reminder','set_completed','move_reminder','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}:
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
        observed=dict(self.reader.read('get',id=public_item['id'],include_native_metadata=True,include_native_tags=True,include_native_hierarchy=True,include_native_children=True,include_native_section=True,include_native_early=True)['item'])
        metadata=observed.pop('native_metadata',None)
        if (observed!=public_item or not isinstance(metadata,dict) or metadata.get('coverage')!=['flagged','tags','hierarchy','children','section','early']
                or metadata.get('section_status') not in {'provider_reported','provider_not_reported'}
                or (metadata.get('section_status')=='provider_not_reported' and metadata.get('section') is not None)
                or (metadata.get('section_status')=='provider_reported' and not isinstance(metadata.get('section'),dict))
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

    def urgent_snapshot(self,public_item):
        observed=dict(self.reader.read('get',id=public_item['id'],include_native_urgent=True)['item']);metadata=observed.pop('native_metadata',None)
        if (observed!=public_item or not isinstance(metadata,dict) or metadata.get('coverage')!=['urgent'] or type(metadata.get('urgent')) is not bool
                or any(metadata.get(key)!=public_item.get(key) for key in ('id','source_id','list_id'))):raise SafetyError('Exact urgent state is unavailable or changed')
        return metadata['urgent']

    def snapshot(self,item):
        if item['operation']=='set_smart_list_sources':
            from .native_reminder_smart_sources import snapshot
            return snapshot(self,item)
        if item['operation']=='rename_smart_list':
            from .native_reminder_smart_rename import snapshot
            return snapshot(self,item)
        if item['operation']=='reparent_reminder':
            from .native_reminder_parents import snapshot
            return snapshot(self,item)
        if item['operation']=='reorder_group':
            from .native_group_order import snapshot
            return snapshot(self,item)
        if item['operation'] in {'delete_reminder','restore_reminder'}:
            from .native_reminder_lifecycle import deletion_snapshot,restoration_snapshot
            return deletion_snapshot(self,item) if item['operation']=='delete_reminder' else restoration_snapshot(self,item)
        if item['operation'] in {'group_list','ungroup_list'}:
            from .native_list_groups import snapshot
            return snapshot(self,item)
        if item['operation']=='rename_group':
            from .native_reminder_groups import rename_snapshot
            return rename_snapshot(self,item)
        if item['operation']=='create_group':
            from .native_reminder_groups import inventory
            return inventory(self,item['source_id'])
        if item['operation']=='reorder_reminder':
            from .native_reminder_order import snapshot
            return snapshot(self,item)
        if item['operation']=='set_list_badge':
            from .native_list_badges import snapshot
            return snapshot(self,item)
        if item['operation']=='set_list_pinned':
            from .native_list_pins import snapshot
            return snapshot(self,item)
        if item['operation']=='move_reminder':
            from .native_reminder_moves import snapshot
            return snapshot(self,item)
        if item['operation'] in {'create_section','rename_section'}:return self.section_snapshot(item)
        if item['operation'] in {'rename_list','set_list_color'}:return self.list_rename_snapshot(item)
        lists=[c for c in self.lists() if c['source_id']==item['source_id']]
        if item['operation'] in {'update_reminder','set_completed','move_reminder','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}:
            reminder=self.reader.read('get',id=item['id'])['item']
            if reminder.get('source_id')!=item['source_id']:raise SafetyError('Reminder belongs to another source')
            current=next((c for c in lists if c['id']==reminder['list_id']),None)
            if not current or not current.get('writable'):raise SafetyError('Reminder list is not writable')
            result={'list':current,'item':reminder}
            native=self.native_snapshot(reminder)
            if native is not None:
                result['native_metadata']=native
                result['urgent']=self.urgent_snapshot(reminder)
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
    def verify_subtask(self,item,outcome):
        if outcome.get('id')!=item['child_id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('parent_id')!=item['id']:return False
        child=self.reader.read('get',id=item['child_id'],include_native_metadata=True,include_native_tags=True,include_native_hierarchy=True,include_native_urgent=True)['item'];metadata=child.get('native_metadata')
        if (child.get('id')!=item['child_id'] or child.get('source_id')!=item['source_id'] or child.get('list_id')!=item['before']['item'].get('list_id')
                or child.get('title')!=item['fields']['title'] or child.get('completed') is not False or child.get('priority')!=0
                or child.get('notes') not in ('',None) or child.get('due') is not None or child.get('start') is not None
                or child.get('has_alarms') is not False or child.get('has_recurrence') is not False or child.get('alarms')!=[] or child.get('recurrence_rules')!=[]
                or child.get('url') is not None or child.get('location') is not None or child.get('public_metadata_complete') is not True or not isinstance(metadata,dict)
                or metadata.get('flagged') is not False or metadata.get('urgent') is not False or metadata.get('tags_complete') is not True or metadata.get('tags')!=[]
                or metadata.get('is_subtask') is not True or metadata.get('parent_id')!=item['id']):return False
        parent=self.reader.read('get',id=item['id'])['item'];baseline=item['before']['item']
        if any(parent.get(k)!=baseline.get(k) for k in ('id','source_id','list_id','title','notes','priority','due','start','completed','url','location','alarms','has_alarms','recurrence_rules','has_recurrence')):return False
        actual=self.native_snapshot(parent);expected=dict(item['before']['native_metadata']);expected['child_ids']=sorted(expected['child_ids']+[item['child_id']]);expected['has_subtasks']=True;expected['children_status']='provider_reported'
        return actual==expected and self.urgent_snapshot(parent) is item['before']['urgent']

    def verify(self,item,outcome):
        if item['operation']=='set_smart_list_sources':
            from .native_reminder_smart_sources import verify
            return verify(self,item,outcome)
        if item['operation']=='rename_smart_list':
            from .native_reminder_smart_rename import verify
            return verify(self,item,outcome)
        if item['operation']=='reparent_reminder':
            from .native_reminder_parents import verify
            return verify(self,item,outcome)
        if item['operation']=='reorder_group':
            from .native_group_order import verify
            return verify(self,item,outcome)
        if item['operation'] in {'delete_reminder','restore_reminder'}:
            from .native_reminder_lifecycle import verify_delete,verify_restore
            return verify_delete(self,item,outcome) if item['operation']=='delete_reminder' else verify_restore(self,item,outcome)
        if item['operation'] in {'group_list','ungroup_list'}:
            from .native_list_groups import verify
            return verify(self,item,outcome)
        if item['operation']=='rename_group':
            from .native_reminder_groups import verify_rename
            return verify_rename(self,item,outcome)
        if item['operation']=='create_group':
            from .native_reminder_groups import verify_create
            return verify_create(self,item,outcome)
        if item['operation']=='reorder_reminder':
            from .native_reminder_order import verify
            return verify(self,item,outcome)
        if item['operation']=='set_list_badge':
            from .native_list_badges import verify
            return verify(self,item,outcome)
        if item['operation']=='set_list_pinned':
            from .native_list_pins import verify
            return verify(self,item,outcome)
        if item['operation']=='move_reminder' and 'source_graph' in item.get('before',{}):
            from .native_reminder_moves import verify
            return verify(self,item,outcome)
        if outcome.get('source_id')!=item['source_id'] or not isinstance(outcome.get('id'),str):return False
        if item['operation']=='rename_section':
            if outcome.get('id')!=item['section_id'] or outcome.get('source_id')!=item['source_id'] or outcome.get('list_id')!=item['list_id']:return False
            observed=self.section_snapshot(item);before=item['before'];expected=[dict(s,title=item['fields']['title']) if s['id'].upper()==item['section_id'].upper() else s for s in before['sections']]
            return observed==dict(before,sections=expected)
        if item['operation']=='create_section':
            if outcome['id']!=item['section_id'] or outcome.get('list_id')!=item['list_id']:return False
            observed=self.section_snapshot(item);before=item['before'];expected=sorted(before['sections']+[{'id':item['section_id'],'title':item['fields']['title']}],key=lambda s:s['id'])
            return observed==dict(before,sections=expected)
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
        if item['operation']=='create_subtask':return self.verify_subtask(item,outcome)
        note=self.reader.read('get',id=outcome['id'])['item']
        if item['operation'] in {'update_reminder','set_completed','move_reminder','set_recurrence','set_flagged','remove_tag','add_tag','replace_tag','set_urgent','add_early','clear_early','create_subtask'}:
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
                if item['operation']=='clear_early':
                    import re
                    fingerprint=native.get('early_payload_fingerprint') if isinstance(native,dict) else None
                    if not isinstance(native,dict) or native.get('early_alerts')!=[] or (fingerprint is not None and (not isinstance(fingerprint,str) or re.fullmatch(r'[0-9a-f]{64}',fingerprint) is None)):return False
                    expected_native.update(early_alerts=[],early_payload_fingerprint=fingerprint)
                if item['operation']=='add_early':
                    units={'minutes':0,'hours':1,'days':2,'weeks':3,'months':4}
                    if not isinstance(native,dict) or native.get('early_alerts')!=[{'native_unit':units[item['fields']['unit']],'native_count':-item['fields']['count']}] or not isinstance(native.get('early_payload_fingerprint'),str):return False
                    expected_native['early_alerts']=native['early_alerts'];expected_native['early_payload_fingerprint']=native['early_payload_fingerprint']
                if item['operation']=='set_flagged':expected_native.update(flagged=item['fields']['flagged'],flag_state=int(item['fields']['flagged']))
                if item['operation']=='remove_tag':expected_native['tags']=[t for t in expected_native['tags'] if t['id'].upper()!=item['fields']['tag_id'].upper()]
                if item['operation'] in {'add_tag','replace_tag'}:
                    tag_id=outcome.get('tag_id');tags=native.get('tags') if isinstance(native,dict) else None
                    if not isinstance(tag_id,str) or not isinstance(tags,list):return False
                    added=[t for t in tags if isinstance(t,dict) and t.get('id')==tag_id]
                    if len(added)!=1 or added[0].get('name')!=item['fields']['tag_name'] or added[0].get('native_type')!=0:return False
                    prior=expected_native['tags']
                    if item['operation']=='replace_tag':prior=[t for t in prior if t['id'].upper()!=item['fields']['tag_id'].upper()]
                    expected_native['tags']=sorted(prior+added,key=lambda t:t['id'])

            if native!=expected_native:return False
            if 'urgent' in item['before'] and self.urgent_snapshot(note) is not (item['fields']['urgent'] if item['operation']=='set_urgent' else item['before']['urgent']):return False
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
