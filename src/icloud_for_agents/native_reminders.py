"""Public EventKit creation adapter, used only through exact native change plans."""
import hashlib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import SafetyError, digest
from .native_access import require_write
from .native_read import NativeReader, native_config


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


class RemindersCreateAdapter:
    operations={'create_list','create_reminder','update_reminder','set_completed','move_reminder'}
    def __init__(self):self.reader=NativeReader('reminders')
    @property
    def backend_id(self):
        helper=native_config('reminders').get('helper_path')
        if not helper:raise SafetyError('Configure native helper before Reminders writes')
        path=Path(helper)
        if not path.is_file() or path.stat().st_size>50_000_000:raise SafetyError('Invalid native helper')
        return 'eventkit-creation-v1:'+hashlib.sha256(path.read_bytes()).hexdigest()
    def lists(self):
        result=[];offset=0
        while True:
            page=self.reader.read('lists',limit=100,offset=offset);result.extend(page['lists'])
            if page.get('done'):return result
            next_offset=page.get('next_offset')
            if type(next_offset) is not int or next_offset<=offset or next_offset>10000:
                raise SafetyError('List discovery incomplete; no write')
            offset=next_offset
    def prepare_existing(self,change):
        allowed={'operation','source_id','id','fields'}
        if set(change)-allowed or not isinstance(change.get('id'),str) or not change['id']:
            raise SafetyError('Existing reminder changes require one exact ID')
        operation=change['operation'];source=change.get('source_id');fields=change.get('fields')
        if source not in require_write('reminders')['source_ids'] or not isinstance(fields,dict) or not fields:
            raise SafetyError('Select an exact source and nonempty fields')
        if operation=='set_completed':
            if set(fields)!={'completed'} or type(fields['completed']) is not bool:
                raise SafetyError('Use an explicit completion boolean')
        elif operation=='move_reminder':
            if set(fields)!={'list_id'} or not isinstance(fields['list_id'],str) or not fields['list_id']:
                raise SafetyError('Select an exact destination list in the same source')
        else:
            if set(fields)-{'title','notes','priority','due','start'}:
                raise SafetyError('Unsupported reminder update fields')
            # Reuse creation validation without injecting defaults into the patch.
            current=self.reader.read('get',id=change['id'])['item']
            merged={key:current[key] for key in ('title','notes','priority')}
            for key in ('due','start'):
                value=current.get(key)
                merged[key]=({k:v for k,v in value.items() if k in {'year','month','day','hour','minute','timezone'} and v is not None}
                             if isinstance(value,dict) else None)
            validated=self.prepare({'operation':'create_reminder','source_id':source,
                'list_id':current['list_id'],'fields':{**merged,**fields}})['fields']
            fields={key:validated[key] for key in fields}
        item={'operation':operation,'source_id':source,'id':change['id'],'fields':dict(fields),
              'fidelity_verified':True,'effects':{'existing_content':'only explicitly listed public EventKit fields change',
              'native_metadata':'advanced native-field preservation requires live acceptance',
              'notifications':'Apple controls sync and notification delivery'}}
        item['before']=self.snapshot(item)
        if item['before']['item'].get('has_recurrence'):
            raise SafetyError('Recurring reminder edits require explicit series/occurrence semantics')
        return item

    def prepare(self,change):
        if isinstance(change,dict) and change.get('operation') in {'update_reminder','set_completed','move_reminder'}:
            return self.prepare_existing(change)
        if not isinstance(change,dict) or set(change)-{'operation','source_id','list_id','fields'}:
            raise SafetyError('Unknown Reminders change fields')
        source=change.get('source_id');operation=change.get('operation');fields=change.get('fields')
        if operation not in self.operations or source not in require_write('reminders')['source_ids']:
            raise SafetyError('Unselected Reminders source or operation')
        allowed={'title'} if operation=='create_list' else {'title','notes','priority','due','start'}
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
            for key in ('due','start'):
                if key in fields:fields[key]=validate_date(fields[key])
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
        if operation=='create_list' and item['before']['matching_titles']>0:raise SafetyError('List title already exists')
        return item
    def snapshot(self,item):
        lists=[c for c in self.lists() if c['source_id']==item['source_id']]
        if item['operation'] in {'update_reminder','set_completed','move_reminder'}:
            reminder=self.reader.read('get',id=item['id'])['item']
            if reminder.get('source_id')!=item['source_id']:raise SafetyError('Reminder belongs to another source')
            current=next((c for c in lists if c['id']==reminder['list_id']),None)
            if not current or not current.get('writable'):raise SafetyError('Reminder list is not writable')
            result={'list':current,'item':reminder}
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
        require_write('reminders');config=native_config('reminders')
        request={'protocol_version':1,'service':'reminders','action':'apply','source_ids':[item['source_id']],'change':item}
        return self.reader._dispatch(request,config)['result']
    def verify(self,item,outcome):
        if outcome.get('source_id')!=item['source_id'] or not isinstance(outcome.get('id'),str):return False
        if item['operation']=='create_list':return any(c['id']==outcome['id'] and c['title']==item['fields']['title'] and c['source_id']==item['source_id'] for c in self.lists())
        note=self.reader.read('get',id=outcome['id'])['item']
        if item['operation'] in {'update_reminder','set_completed','move_reminder'}:
            if outcome['id']!=item['id'] or note.get('source_id')!=item['source_id']:return False
            expected=dict(item['before']['item']);expected.update(item['fields'])
            for key in ('title','notes','priority','due','start','list_id','completed','url','has_recurrence','has_alarms'):
                actual=note.get(key);wanted=expected.get(key)
                if key in {'due','start'} and isinstance(wanted,dict):
                    if not isinstance(actual,dict) or any(actual.get(k)!=v for k,v in wanted.items()):return False
                elif actual!=wanted:return False
            return True
        if note.get('source_id')!=item['source_id'] or note.get('list_id')!=item['list_id'] or note.get('completed') is not False:return False
        if any(note.get(k)!=item['fields'][k] for k in ('title','notes','priority')):return False
        for key in ('due','start'):
            expected=item['fields'].get(key);actual=note.get(key)
            if expected is None:
                if actual is not None:return False
            elif not isinstance(actual,dict) or any(actual.get(k)!=v for k,v in expected.items()):return False
        return True
