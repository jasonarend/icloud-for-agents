"""Exact saved-template metadata; passive sharing URLs require explicit selection."""
import math
from urllib.parse import urlsplit
from .core import SafetyError
from .native_reminder_files import validate_id

KEYS={'protocol_version','source_id','id','title','minimum_supported_version','configuration','public_link_present','public_link_included','public_link','data_mutated','backend'}
LINK_KEYS={'url','can_be_updated','creationDate','lastModifiedDate','expirationDate','configuration'}
FLAGS={'shouldIncludeHashtags','shouldIncludeAlarmTriggersBasedOnDateOrTimeInterval','shouldIncludeAlarmTriggersBasedOnLocationOrVehicle'}


def validate(value,source_id,identity,include_public_link):
    if (not isinstance(value,dict)or set(value)!=KEYS or type(value['protocol_version'])is not int or value['protocol_version']!=1
            or value['source_id']!=source_id or value['id']!=identity or not isinstance(value['title'],str)or len(value['title'])>1000
            or type(value['minimum_supported_version'])is not int or not 0<=value['minimum_supported_version']<=2147483647
            or type(value['public_link_present'])is not bool or value['public_link_included']is not include_public_link
            or value['data_mutated']is not False or value['backend']!='reminderkit-template-metadata-v1'):
        raise SafetyError('Exact bounded template metadata did not verify')
    try:
        if len(value['title'].encode('utf-8',errors='strict'))>4000:raise ValueError()
    except (UnicodeError,ValueError):raise SafetyError('Template metadata text is invalid')from None
    config=value['configuration']
    if config is not None:
        if not isinstance(config,dict)or set(config)!={'source_list_id','save_completed'}or type(config['save_completed'])is not bool:raise SafetyError('Template configuration is incomplete')
        validate_id(config['source_list_id'])
    link=value['public_link']
    if not include_public_link or not value['public_link_present']:
        if link is not None:raise SafetyError('Unrequested or absent template link must not be returned')
    else:
        if (not isinstance(link,dict)or set(link)!=LINK_KEYS or not isinstance(link['url'],str)or not 1<=len(link['url'])<=4096
                or any(ord(c)<33 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in link['url'])or type(link['can_be_updated'])is not bool
                or not isinstance(link['configuration'],dict)or set(link['configuration'])!=FLAGS
                or any(type(v)is not bool for v in link['configuration'].values())):raise SafetyError('Template link metadata is invalid')
        try:
            url=urlsplit(link['url'])
            if url.scheme!='https'or not url.hostname or url.username is not None or url.password is not None:raise ValueError()
            url.port
        except ValueError:raise SafetyError('Template sharing URL is not a bounded passive HTTPS link')from None
        for key in ('creationDate','lastModifiedDate','expirationDate'):
            v=link[key]
            if v is not None and(type(v)not in(int,float)or not math.isfinite(v)or not 0<=v<=253402300799):raise SafetyError('Template link date is unavailable or invalid')
    return value


def inspect(source_id,identity,*,include_public_link=False,reader=None):
    from .native_once import selected_read_access
    from .native_read import NativeReader,native_config
    validate_id(source_id);validate_id(identity)
    if type(include_public_link)is not bool:raise SafetyError('Explicit include_public_link must be Boolean')
    access=selected_read_access('reminders');config=native_config('reminders')
    if source_id not in config.get('source_ids',[])or config.get('advanced_backend')!='reminderkit'or not config.get('helper_path'):raise SafetyError('Template metadata requires an exact selected source and updated ReminderKit helper')
    reader=reader or NativeReader('reminders');fields={'source_id':source_id,'id':identity,'include_public_link':include_public_link}
    before=validate(reader.read('template_metadata',**fields),source_id,identity,include_public_link)
    if reader.read('template_metadata',**fields)!=before or native_config('reminders')!=config or selected_read_access('reminders')!=access:raise SafetyError('Template metadata, access or helper configuration changed during observation')
    return before|{'source_list_reference_resolved':False,'template_contents_complete':False,'sharing_link_fetched':False,'instructions_trusted':False,
                   'consistency':'separate_live_reads_may_change','sharing_scope':'Observed local saved link only; no creation, update, revocation, recipient access, availability or cloud freshness is verified'}
