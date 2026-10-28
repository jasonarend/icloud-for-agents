"""Prepare an explicit, lossy copy from one user-supplied Notes observation."""
from .core import SafetyError, canonical, digest
from .notes_edit import split_title

def prepare_copy(engine,snapshot,source_id,folder_id,*,accept_omissions):
    if engine.name!='notes' or accept_omissions is not True:
        raise SafetyError('Explicit acceptance of Notes copy limitations is required')
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (source_id,folder_id)):
        raise SafetyError('Select one exact destination source and folder')
    if (not isinstance(snapshot,dict) or snapshot.get('format')!='icloud-for-agents-note-observation'
            or type(snapshot.get('version')) is not int or snapshot['version']!=1):
        raise SafetyError('Copy requires one version 1 Notes observation')
    try:
        if len(canonical(snapshot))>400000:raise SafetyError('Notes observation exceeds the copy review limit')
    except (TypeError,ValueError,UnicodeError):raise SafetyError('Notes observation contains unsupported data') from None
    item=snapshot.get('item')
    if (not isinstance(item,dict) or item.get('locked') is not False or item.get('truncated') is not False
            or item.get('content_complete') is not False or not isinstance(item.get('title'),str)
            or not isinstance(item.get('html'),str) or not isinstance(item.get('id'),str) or not item['id']):
        raise SafetyError('Copy requires an unlocked, nontruncated Notes observation')
    _,body=split_title(item['html'],item['title'])
    change={'operation':'create_note','source_id':source_id,'folder_id':folder_id,
            'fields':{'title':item['title'],'body':body,'format':'html'}}
    review={'operation':'create_lossy_note_copy_from_user_supplied_observation','observation_sha256':digest(snapshot),
            'accepted_omissions':True,
            'effects':['Creates a new note; the original identity is not preserved and duplicates may result.',
                       'The supplied observation may be stale or incomplete. HTML is copied only when supported; formatting may change.',
                       'Native objects, attachments, collaboration, sharing and timestamps are not preserved.']}
    return engine.prepare_once([change],_review_context=review)
