"""Read-only RFC 5545 VTODO projection of a deliberately narrow public subset."""
import hashlib
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .core import SafetyError

def _text(value):
    if not isinstance(value,str) or any((ord(c)<32 and c not in '\r\n\t') or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in value):
        raise SafetyError('Reminder text cannot be represented safely in iCalendar')
    return value.replace('\\','\\\\').replace('\r\n','\n').replace('\r','\n').replace('\n','\\n').replace(';','\\;').replace(',','\\,')

def _date(value):
    if value is None:return None
    if not isinstance(value,dict) or type(value.get('date_only')) is not bool:raise SafetyError('Reminder date semantics are unavailable')
    try:
        for key in ('year','month','day'):
            if type(value.get(key)) is not int:raise ValueError()
        day=date(value['year'],value['month'],value['day'])
        if value['date_only']:
            if any(value.get(k) is not None for k in ('hour','minute','second')):raise ValueError()
            return 'DATE',f'{day.year:04d}{day.month:02d}{day.day:02d}'
        for key in ('hour','minute','second'):
            if type(value.get(key)) is not int:raise ValueError()
        zone_name=value.get('timezone')
        if not isinstance(zone_name,str) or not 1<=len(zone_name)<=128:raise ValueError()
        zone=ZoneInfo(zone_name)
        local=datetime(day.year,day.month,day.day,value['hour'],value['minute'],value['second'])
        instants=set()
        for fold in (0,1):
            candidate=local.replace(tzinfo=zone,fold=fold).astimezone(timezone.utc)
            if candidate.astimezone(zone).replace(tzinfo=None)==local:instants.add(candidate)
        if len(instants)!=1:raise ValueError()
        instant=instants.pop()
        return 'UTC',f'{instant.year:04d}{instant.month:02d}{instant.day:02d}T{instant.hour:02d}{instant.minute:02d}{instant.second:02d}Z'
    except (ValueError,OverflowError,ZoneInfoNotFoundError):raise SafetyError('Only complete date-only or unambiguous zoned reminder dates can be exported') from None

def _fold(line):
    # Fold at character boundaries; continuation whitespace counts toward 75 octets.
    output=[];current='';width=0
    for character in line:
        n=len(character.encode('utf-8'))
        if width+n>75:output.append(current);current=' ';width=1
        current+=character;width+=n
    output.append(current)
    return '\r\n'.join(output)+'\r\n'

def export_ical(reader,identity,now=None):
    if not isinstance(identity,str) or not 1<=len(identity)<=1000:raise SafetyError('Select one exact reminder')
    item=reader.read('get',id=identity)['item']
    if (item.get('id')!=identity or not isinstance(item.get('source_id'),str) or not item['source_id']
            or type(item.get('completed')) is not bool or type(item.get('priority')) is not int or item['priority'] not in (0,1,5,9)
            or item.get('public_metadata_complete') is not True or item.get('content_complete') is not False
            or item.get('has_recurrence') is not False or item.get('has_alarms') is not False
            or item.get('recurrence_rules')!=[] or item.get('alarms')!=[] or any(k not in item for k in ('due','start','url','location'))):
        raise SafetyError('iCalendar export requires complete public metadata and no recurrence or alarms')
    title=_text(item.get('title'));notes=_text(item.get('notes'));start=_date(item['start']);due=_date(item['due'])
    if start and due and start[0]!=due[0]:raise SafetyError('Start and due dates must use the same iCalendar value type')
    if start and due and due[1]<=start[1]:raise SafetyError('iCalendar due must be later than its start')
    stamp=now or datetime.now(timezone.utc)
    if not isinstance(stamp,datetime) or stamp.tzinfo is None:raise SafetyError('An aware export timestamp is required')
    stamp=stamp.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:uid=hashlib.sha256((item['source_id']+'\0'+identity).encode('utf-8')).hexdigest()+'@icloud-for-agents.invalid'
    except UnicodeError:raise SafetyError('Reminder identity is not valid Unicode') from None
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//iCloud for Agents//Reminder Observation//EN',
           'BEGIN:VTODO','UID:'+uid,'DTSTAMP:'+stamp,'SUMMARY:'+title,'DESCRIPTION:'+notes,
           'STATUS:'+('COMPLETED' if item['completed'] else 'NEEDS-ACTION'),'PRIORITY:'+str(item['priority'])]
    if item['location'] is not None:lines.append('LOCATION:'+_text(item['location']))
    if item['url'] is not None:
        from .native_reminders import validate_link
        lines.append('URL:'+validate_link(item['url']))
    for name,value in (('DTSTART',start),('DUE',due)):
        if value:lines.append(name+(';VALUE=DATE' if value[0]=='DATE' else '')+':'+value[1])
    lines+=['END:VTODO','END:VCALENDAR']
    try:content=''.join(_fold(line) for line in lines)
    except UnicodeError:raise SafetyError('Reminder text is not valid Unicode') from None
    if len(content.encode('utf-8'))>1_000_000:raise SafetyError('iCalendar export exceeds one million bytes')
    return {'format':'text/calendar','content':content,'coverage':{'restorable':False,'complete_backup':False,
            'native_metadata_included':False,'limitations':['Native tags, attachments, hierarchy and advanced metadata are omitted.',
             'Timestamps, original time-zone labels and collaboration metadata are omitted.','Only one nonrecurring reminder without alarms and with supported date semantics is represented; clients may interpret date-only due dates differently.',
             'The generated UID is a pseudonymous identifier for this observation, not a promise of portable identity.']}}
