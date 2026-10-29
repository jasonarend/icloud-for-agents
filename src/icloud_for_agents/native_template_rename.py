"""One exact private saved-template label; preserve observed content and sections."""
from copy import deepcopy
from .core import SafetyError
from .native_once import planning_access
from .native_template_metadata import validate
from .native_reminder_template_store import outline

COVERAGE='exact_private_saved_template_metadata_sections_and_bounded_saved_item_outline'


def snapshot(adapter,item):
    source=item['source_id'];identity=item['id'];fields={'source_id':source,'id':identity,'include_public_link':False}
    metadata=validate(adapter.reader.read('template_metadata',**fields),source,identity,False)
    if metadata['public_link_present']:raise SafetyError('Template renaming currently requires no observed public sharing link')
    sections=adapter.reader.read('template_sections',source_id=source,id=identity)
    from .native_read import validate_template_sections
    validate_template_sections(sections,source,identity)
    content=outline(source,identity,limit=20,offset=0,reader=adapter.reader)
    if content['done']is not True or content['total_items']>20 or content['title']!=metadata['title']:raise SafetyError('Template rename requires one complete local page of at most twenty saved-item outlines and an unchanged title')
    if adapter.reader.read('template_metadata',**fields)!=metadata or adapter.reader.read('template_sections',source_id=source,id=identity)!=sections:raise SafetyError('Template changed during rename review')
    return {'metadata':metadata,'sections':sections,'outline':content}


def prepare(adapter,change):
    if (not isinstance(change,dict)or set(change)!={'operation','source_id','id','fields'}or change.get('operation')!='rename_template'
            or change['source_id']not in planning_access('reminders')['source_ids']or not isinstance(change['fields'],dict)or set(change['fields'])!={'title'}):raise SafetyError('Select one exact saved template/source and fields.title')
    title=change['fields']['title']
    if not isinstance(title,str)or not title.strip()or len(title)>1000 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in title):raise SafetyError('Template title must be bounded nonempty text without controls')
    if len(title.encode())>4000:raise SafetyError('Template title exceeds UTF-8 bound')
    item=deepcopy(change);item['before']=snapshot(adapter,item)
    if item['before']['metadata']['title']==title:raise SafetyError('Template already has the requested title')
    item.update(fidelity_verified=True,effects={'change':'Rename only this exact saved template; no list is instantiated and no saved item is edited.',
       'preservation':'Native staging must equal a copy of the complete template storage with only its name changed, track exactly one template name change and no other tracked object. Readback preserves native metadata, sections and the bounded local saved-item outline.',
       'scope':'Selected account, no observed public link, optional ReminderKit helper and explicitly opted-in read-only store; at most twenty saved-item outlines. Quota is one template.',
       'limits':'Outline omits bodies/rich fields; native name-only staging does not certify hidden provider effects, global sharing absence, cloud freshness or atomicity. No template deletion or sharing operation is included.','quota_objects':1})
    return item


def record_matches(item,observed):
    try:
        before=item['before'];after=observed['snapshot'];expected=deepcopy(before);expected['metadata']['title']=item['fields']['title'];expected['outline']['title']=item['fields']['title']
        revision=after['outline']['template_revision']
        if type(revision)is not int or revision<before['outline']['template_revision']:return False
        expected['outline']['template_revision']=revision
        return item['operation']=='rename_template'and observed.get('state')=='saved_template_renamed'and observed.get('id')==item['id']and observed.get('source_id')==item['source_id']and after==expected
    except(KeyError,TypeError,AttributeError):return False


def expected_observation(adapter,item):
    value={'state':'saved_template_renamed','source_id':item['source_id'],'id':item['id'],'snapshot':snapshot(adapter,item)}
    if not record_matches(item,value):raise SafetyError('Exact template name and preserved metadata/sections/outline did not verify')
    return value


def verify(adapter,item,outcome):
    if any(outcome.get(k)!=item[k]for k in('source_id','id')):return False
    expected_observation(adapter,item);return True
