"""Prepare a reviewed, deliberately lossy copy of an observation page."""
from .core import SafetyError, canonical, digest
from .native_reminders import validate_date

def _date(value):
    if value is None:return None
    if (not isinstance(value,dict) or set(value)-{'year','month','day','hour','minute','second','timezone','date_only'}
            or type(value.get('date_only')) is not bool):raise SafetyError('Copy requires explicit observed date semantics')
    fields={key:value[key] for key in ('year','month','day') if key in value}
    if value['date_only']:
        if any(value.get(key) is not None for key in ('hour','minute','second')):raise SafetyError('Date-only copy has inconsistent time')
    else:
        if type(value.get('second')) is not int or value['second']!=0:raise SafetyError('Copy requires an observed whole-minute Reminder time')
        for key in ('hour','minute','timezone'):
            if key not in value or value[key] is None:raise SafetyError('Timed copy requires an explicit timezone and minute')
            fields[key]=value[key]
    return validate_date(fields)

def prepare_copy(engine,snapshot,source_id,list_id,*,accept_omissions,include_dates=False,include_urls=False,grant_id=None):
    if engine.name!='reminders' or accept_omissions is not True or type(include_dates) is not bool or type(include_urls) is not bool:
        raise SafetyError('Explicit acceptance of copy limitations and Boolean options are required')
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (source_id,list_id)):raise SafetyError('Select an exact destination source and list')
    if not isinstance(snapshot,dict) or snapshot.get('format')!='icloud-for-agents-reminders-observation' or type(snapshot.get('version')) is not int or snapshot['version']!=1:
        raise SafetyError('Copy requires one version 1 Reminder observation page')
    rows=snapshot.get('items');page=snapshot.get('page')
    if (not isinstance(rows,list) or not 1<=len(rows)<=10 or not isinstance(page,dict)
            or type(page.get('done')) is not bool or not isinstance(snapshot.get('list_id'),str)):
        raise SafetyError('Copy requires 1..10 observed items in one page')
    try:
        if len(canonical(snapshot))>100000:raise SafetyError('Observation page exceeds the copy review limit')
    except (TypeError,ValueError,UnicodeError):raise SafetyError('Observation contains unsupported data') from None
    changes=[];seen=set();completed=0
    for row in rows:
        if (not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in seen
                or row.get('list_id')!=snapshot['list_id'] or type(row.get('completed')) is not bool
                or not isinstance(row.get('title'),str) or not isinstance(row.get('notes'),str)
                or type(row.get('priority')) is not int or row['priority'] not in (0,1,5,9)):
            raise SafetyError('Observed reminder copy fields or identities are invalid')
        seen.add(row['id']);completed+=int(row['completed'])
        fields={key:row[key] for key in ('title','notes','priority')}
        if include_dates:
            if 'due' not in row or 'start' not in row:raise SafetyError('Date copy requires both observed date fields')
            for key in ('due','start'):
                value=_date(row[key])
                if value is not None:fields[key]=value
        if include_urls:
            if 'url' not in row:raise SafetyError('URL copy requires the observed URL field')
            if row['url'] is not None:fields['url']=row['url']
        changes.append({'operation':'create_reminder','source_id':source_id,'list_id':list_id,'fields':fields})
    review={'operation':'create_lossy_copies_from_user_supplied_observation','observation_sha256':digest(snapshot),
            'item_count':len(rows),'source_completed_count':completed,'include_dates':include_dates,'include_urls':include_urls,
            'page_done':page['done'],'accepted_omissions':True,
            'effects':['Creates new incomplete reminders; original IDs and completion state are not preserved.',
                       'Native tags, hierarchy, attachments, recurrence, alarms, location, sharing and timestamps are not copied.',
                       'Copies can duplicate existing reminders; the source observation is user-supplied and may be stale or incomplete.',
                       'Dates, if selected, can affect due-date behavior; notification delivery remains controlled by Apple.']}
    if grant_id is not None:
        if not isinstance(grant_id,str) or not grant_id:raise SafetyError('Use one exact active copy scenario grant')
        return engine.prepare_with_grant(grant_id,changes,_review_context=review)
    return engine.prepare_once(changes,_review_context=review)
