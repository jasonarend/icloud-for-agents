"""Strict single-VTODO iCalendar input for reviewed, lossy Reminder copies."""
import hashlib,re
from datetime import datetime,timezone
from .core import SafetyError
from .native_reminder_import import prepare_copy

def _text(value):
    result=[];index=0
    while index<len(value):
        char=value[index]
        if char=='\\':
            index+=1
            if index>=len(value) or value[index] not in '\\,;nN':raise SafetyError('Unsupported iCalendar text escape')
            result.append('\n' if value[index] in 'nN' else value[index])
        else:
            if (ord(char)<32 and char!='\t') or ord(char)==127 or 0xD800<=ord(char)<=0xDFFF or char in ',;':raise SafetyError('Invalid or unescaped iCalendar text character')
            result.append(char)
        index+=1
    return ''.join(result)

def _utc(value):
    if not re.fullmatch(r'\d{8}T\d{6}Z',value):raise SafetyError('iCalendar timestamp requires explicit UTC')
    try:return datetime.strptime(value,'%Y%m%dT%H%M%SZ')
    except ValueError:raise SafetyError('Invalid iCalendar UTC timestamp') from None

def _date(value,parameter):
    if parameter==';VALUE=DATE':
        if not re.fullmatch(r'\d{8}',value):raise SafetyError('Invalid iCalendar date')
        try:day=datetime.strptime(value,'%Y%m%d')
        except ValueError:raise SafetyError('Invalid iCalendar date') from None
        return {'year':day.year,'month':day.month,'day':day.day,'date_only':True,'timezone':None}
    if parameter:raise SafetyError('Unsupported iCalendar date parameter')
    moment=_utc(value)
    if moment.second!=0:raise SafetyError('Subminute iCalendar times cannot be copied')
    return {'year':moment.year,'month':moment.month,'day':moment.day,'hour':moment.hour,'minute':moment.minute,
            'second':0,'timezone':'UTC','date_only':False}

def observation(text):
    if not isinstance(text,str):raise SafetyError('iCalendar input must be text')
    try:data=text.encode('utf-8')
    except UnicodeError:raise SafetyError('iCalendar input must be valid Unicode') from None
    if (not 1<=len(data)<=1_000_000 or not text.endswith('\r\n') or re.search(r'(?<!\r)\n|\r(?!\n)',text)
            or any((ord(c)<32 and c not in '\r\n\t') or ord(c)==127 for c in text)):
        raise SafetyError('iCalendar input requires bounded CRLF-delimited text')
    physical=text[:-2].split('\r\n');segments=[]
    if len(physical)>10000:raise SafetyError('iCalendar component exceeds line bounds')
    for line in physical:
        if line.startswith((' ','\t')):
            if not segments:raise SafetyError('Invalid iCalendar line folding')
            segments[-1].append(line[1:])
        else:segments.append([line])
    logical=[''.join(parts) for parts in segments]
    if len(logical)>100 or any(len(line)>100000 for line in logical):raise SafetyError('iCalendar component exceeds input bounds')
    if len(logical)<7 or logical[0].upper()!='BEGIN:VCALENDAR' or logical[-1].upper()!='END:VCALENDAR':
        raise SafetyError('Expected one complete iCalendar object')
    calendar={};todo={};alarms=[];alarm=None;state='calendar';closed=False
    cal_allowed={'VERSION','PRODID','CALSCALE'}
    todo_allowed={'UID','DTSTAMP','SUMMARY','DESCRIPTION','STATUS','PRIORITY','DTSTART','DUE','URL','LOCATION','CREATED','LAST-MODIFIED','COMPLETED'}
    for line in logical[1:-1]:
        upper=line.upper()
        if upper=='BEGIN:VTODO' and state=='calendar' and not closed:state='todo';continue
        if upper=='END:VTODO' and state=='todo':state='calendar';closed=True;continue
        if upper=='BEGIN:VALARM' and state=='todo':
            if len(alarms)>=10:raise SafetyError('Too many iCalendar alarms')
            alarm={};state='alarm';continue
        if upper=='END:VALARM' and state=='alarm':
            if set(alarm)!={'ACTION','TRIGGER','DESCRIPTION'} or alarm['ACTION'][0].upper()!='DISPLAY' or alarm['TRIGGER'][1]!=';VALUE=DATE-TIME':
                raise SafetyError('Only absolute display alarms are supported')
            try:seconds=int(_utc(alarm['TRIGGER'][0]).replace(tzinfo=timezone.utc).timestamp())
            except (ValueError,OverflowError,OSError):raise SafetyError('Absolute alarm timestamp is not representable') from None
            if not 946684800<=seconds<=4102444799:raise SafetyError('Absolute alarm must be within 2000 through 2099')
            _text(alarm['DESCRIPTION'][0]);alarms.append({'absolute_date':seconds});alarm=None;state='todo';continue
        if ':' not in line or (state=='calendar' and closed):raise SafetyError('Unsupported iCalendar component or property')
        left,value=line.split(':',1);name,sep,param=left.partition(';');name=name.upper();parameter=(';'+param.upper()) if sep else ''
        target=calendar if state=='calendar' else todo if state=='todo' else alarm
        allowed=cal_allowed if state=='calendar' else todo_allowed if state=='todo' else {'ACTION','TRIGGER','DESCRIPTION'}
        parameter_allowed=(state=='todo' and name in {'DTSTART','DUE'}) or (state=='alarm' and name=='TRIGGER')
        if name not in allowed or name in target or (parameter and not parameter_allowed):
            raise SafetyError('Unsupported, repeated or parameterized iCalendar property')
        target[name]=(value,parameter)
    if state!='calendar' or not closed or calendar.get('VERSION')!=('2.0','') or 'PRODID' not in calendar:
        raise SafetyError('Incomplete iCalendar VTODO')
    if 'CALSCALE' in calendar and calendar['CALSCALE'][0].upper()!='GREGORIAN':raise SafetyError('Unsupported iCalendar calendar scale')
    if not {'UID','DTSTAMP','SUMMARY','STATUS','PRIORITY'}<=set(todo):raise SafetyError('iCalendar task lacks explicit required copy fields')
    if not todo['UID'][0] or len(todo['UID'][0])>1000:raise SafetyError('Invalid iCalendar task UID')
    for key in ('DTSTAMP','CREATED','LAST-MODIFIED','COMPLETED'):
        if key in todo:_utc(todo[key][0])
    status=todo['STATUS'][0].upper()
    if status not in {'NEEDS-ACTION','COMPLETED'} or ('COMPLETED' in todo and status!='COMPLETED'):
        raise SafetyError('Unsupported or inconsistent iCalendar task status')
    priority=todo['PRIORITY'][0]
    if priority not in {'0','1','5','9'}:raise SafetyError('Unsupported iCalendar priority')
    title=_text(todo['SUMMARY'][0]);notes=_text(todo.get('DESCRIPTION',('', ''))[0])
    due=_date(*todo['DUE']) if 'DUE' in todo else None;start=_date(*todo['DTSTART']) if 'DTSTART' in todo else None
    if due and start:
        if due['date_only']!=start['date_only']:raise SafetyError('iCalendar start and due value types must match')
        keys=('year','month','day') if due['date_only'] else ('year','month','day','hour','minute','second')
        if tuple(due[k] for k in keys)<=tuple(start[k] for k in keys):raise SafetyError('iCalendar due must be later than its start')
    if 'LOCATION' in todo:_text(todo['LOCATION'][0])
    row={'id':hashlib.sha256(todo['UID'][0].encode('utf-8')).hexdigest(),'list_id':'user-supplied-icalendar',
         'title':title,'notes':notes,'priority':int(priority),'completed':status=='COMPLETED',
         'due':due,'start':start,'url':todo.get('URL',(None,''))[0]}
    return {'format':'icloud-for-agents-reminders-observation','version':1,'list_id':'user-supplied-icalendar',
            'page':{'done':True},'items':[row],'source_absolute_alarms':alarms},hashlib.sha256(data).hexdigest()

def prepare_ical_copy(engine,calendar_text,source_id,list_id,*,accept_omissions,include_dates=False,include_urls=False,include_alarms=False,grant_id=None):
    snapshot,source_hash=observation(calendar_text)
    return prepare_copy(engine,snapshot,source_id,list_id,accept_omissions=accept_omissions,
                        include_dates=include_dates,include_urls=include_urls,grant_id=grant_id,_source_ical_sha256=source_hash,
                        include_alarms=include_alarms,_ical_alarms=snapshot['source_absolute_alarms'])
