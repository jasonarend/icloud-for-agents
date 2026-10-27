"""Bounded Gregorian EventKit rule contracts; no occurrence expansion or emulation."""
from .core import SafetyError

ARRAYS={'days_of_month':31,'months_of_year':12,'weeks_of_year':53,'days_of_year':366,'set_positions':366}
KEYS={'frequency','interval','end','days_of_week',*ARRAYS}

def validate_recurrence(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-KEYS or not {'frequency','interval','end'}<=set(value):
        raise SafetyError('A rule requires explicit frequency, interval and end; use null to remove it')
    frequency=value['frequency'];interval=value['interval'];end=value['end']
    if not isinstance(frequency,str) or frequency not in {'daily','weekly','monthly','yearly'} or type(interval) is not int or not 1<=interval<=999:
        raise SafetyError('Use daily/weekly/monthly/yearly with an integer interval 1..999')
    if end is not None:
        if not isinstance(end,dict) or len(end)!=1:raise SafetyError('Recurrence end is null, count, or absolute date in Unix seconds')
        key=next(iter(end));number=end[key]
        if key not in {'count','date'} or type(number) is not int or not (1<=number<=10000 if key=='count' else 946684800<=number<=4102444799):
            raise SafetyError('Invalid finite recurrence end')
        end=dict(end)
    result={'frequency':frequency,'interval':interval,'end':end}
    days=value.get('days_of_week',[])
    if not isinstance(days,list) or len(days)>53:raise SafetyError('Bounded recurrence weekdays required')
    unique=set()
    for day in days:
        if not isinstance(day,dict) or set(day)!={'weekday','week_number'}:raise SafetyError('Use weekday and explicit week_number (0 means every matching weekday)')
        weekday,week=day['weekday'],day['week_number'];maximum=53 if frequency=='yearly' else 5
        if (type(weekday) is not int or not 1<=weekday<=7 or type(week) is not int or abs(week)>maximum
                or frequency=='daily' or (frequency=='weekly' and week!=0) or (weekday,week) in unique):
            raise SafetyError('Invalid, duplicate or ignored recurrence weekday')
        unique.add((weekday,week))
    result['days_of_week']=[{'weekday':d,'week_number':w} for d,w in sorted(unique)]
    for key,maximum in ARRAYS.items():
        values=value.get(key,[])
        if (not isinstance(values,list) or len(values)>366 or any(type(n) is not int or n==0 or abs(n)>maximum or (key=='months_of_year' and n<1) for n in values)
                or len(set(values))!=len(values)):
            raise SafetyError('Invalid bounded recurrence filter')
        if values and ((key=='days_of_month' and frequency!='monthly') or (key in {'months_of_year','weeks_of_year','days_of_year'} and frequency!='yearly')):
            raise SafetyError('This recurrence filter would be ignored for the selected frequency')
        result[key]=sorted(values)
    if result['set_positions'] and not any(result[k] for k in ('days_of_week','days_of_month','months_of_year','weeks_of_year','days_of_year')):
        raise SafetyError('Set positions require explicit recurrence filters')
    return result


def observed_rule(value):
    if (not isinstance(value,dict) or value.get('fields_complete') is not True or value.get('calendar_identifier')!='gregorian'
            or set(value)!=(KEYS|{'fields_complete','calendar_identifier','first_weekday'})):
        raise SafetyError('Complete supported Gregorian recurrence metadata required')
    rule={k:value[k] for k in KEYS};end=rule['end']
    if end is not None:
        if not isinstance(end,dict) or set(end)!={'count','date'}:raise SafetyError('Incomplete native recurrence end')
        rule['end']={'date':int(end['date'])} if end['date'] is not None and type(end['date']) in (int,float) and int(end['date'])==end['date'] and end['count']==0 else {'count':end['count']} if end['date'] is None else {'invalid':None}
    normalized=validate_recurrence(rule)
    # RFC default (0) and explicit Monday (2) have the same week boundary.
    if type(value['first_weekday']) is not int or value['first_weekday'] not in {0,2}:raise SafetyError('Custom week starts cannot be written by this EventKit route')
    return normalized


def recurrence_equal(actual,expected):
    try:return isinstance(actual,list) and len(actual)==(0 if expected is None else 1) and (not actual or observed_rule(actual[0])==expected)
    except (SafetyError,ValueError,TypeError,OverflowError):return False


def rule_effects(rule):
    return {'scope':'recurrence configuration of the exact uncompleted native reminder object',
            'rule':rule,'history':'No historical or separately generated reminder is edited; occurrence-scoped changes and completion generation are not implemented by this operation',
            'notifications':'Changing repetition may change future native scheduling; notification delivery remains controlled by Apple',
            'week_start':'Gregorian; Monday, including the native default week boundary'}


def validate_seed(rule,due):
    if rule is None:return
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if not isinstance(due,dict):raise SafetyError('A repeat rule requires an explicit due date')
    from .native_reminders import validate_date
    due=validate_date({k:v for k,v in due.items() if k in {'year','month','day','hour','minute','timezone'} and v is not None})
    if rule['end'] and 'date' in rule['end']:
        if 'hour' not in due or not due.get('timezone'):raise SafetyError('An absolute recurrence end requires a timed due date and timezone')
        seed=datetime(due['year'],due['month'],due['day'],due['hour'],due['minute'],tzinfo=ZoneInfo(due['timezone'])).timestamp()
        if rule['end']['date']<seed:raise SafetyError('Recurrence end precedes the first due instant')
