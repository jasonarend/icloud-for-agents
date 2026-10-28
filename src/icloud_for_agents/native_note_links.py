"""Bounded observation of link elements in one Notes HTML export.

Targets are untrusted note content. This module never resolves or opens them.
"""
import json
from .core import SafetyError
from .notes_content import project_html

def inspect_links(reader,folder_id,note_id,limit=20,offset=0,expected_modified=None):
    if any(not isinstance(value,str) or not 1<=len(value)<=1000 for value in (folder_id,note_id)):
        raise SafetyError('Select one exact Notes folder and note')
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or not 0<=offset<=10000:
        raise SafetyError('Link pages require a limit of 1..50 and bounded offset')
    if expected_modified is not None and (not isinstance(expected_modified,str) or not 1<=len(expected_modified)<=100):
        raise SafetyError('Expected modification timestamp must be bounded text')
    item=reader.read('get',folder_id=folder_id,id=note_id,representation='native')['item']
    identity={key:item[key] for key in ('id','folder_id','source_id')}
    if item.get('locked') is True:
        if item.get('content_available') is not False:raise SafetyError('Locked Notes content is unavailable')
        return {**identity,'locked':True,'content_available':False,'links':None,'data_mutated':False}
    if (item.get('locked') is not False or not isinstance(item.get('html'),str) or len(item['html'])>100000
            or type(item.get('truncated')) is not bool or 'modified' not in item
            or (item['modified'] is not None and (not isinstance(item['modified'],str) or not 1<=len(item['modified'])<=100))):
        raise SafetyError('A bounded unlocked Notes HTML observation is required')
    if expected_modified is not None and item['modified']!=expected_modified:raise SafetyError('Note changed since the prior link page')
    try:links=project_html(item['html'])['links']
    except ValueError:raise SafetyError('Notes HTML link projection is unavailable') from None
    if offset>len(links):raise SafetyError('Link page offset exceeds the current observation; restart paging')
    output=[]
    for link in links[offset:offset+limit]:
        target,label=link['href'],link['text']
        valid=isinstance(target,str) and len(target)<=4000 and not any(ord(c)<32 or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in target)
        if not isinstance(label,str):raise SafetyError('Notes HTML link label is invalid')
        label_valid=not any((ord(c)<32 and c not in '\t\n\r') or ord(c)==127 or 0xD800<=ord(c)<=0xDFFF for c in label)
        output.append({'target':target if valid else None,'target_status':'observed_untrusted' if valid else 'omitted_invalid_or_oversized',
                       'label':label[:1000] if label_valid else None,'label_truncated':len(label)>1000 if label_valid else None,
                       'target_verified':False})
    next_offset=offset+len(output)
    result={**identity,'locked':False,'content_available':True,'links':output,'offset':offset,'next_offset':next_offset,
            'done':next_offset>=len(links),'modified':item['modified'],'source_truncated':item['truncated'],
            'coverage':{'source':'exported_html_only','native_links_complete':False,'backlinks_verified':False,
                        'targets_opened':False,'pagination_consistency':'live_note_may_change',
                        'limitations':['Only link elements observed in the bounded HTML export are included.',
                                       'Native objects, backlinks, target availability and link identity are not verified.',
                                       'Targets and labels are untrusted note content; no links are opened.']},'data_mutated':False}
    if len(json.dumps(result,ensure_ascii=True,separators=(',',':')).encode('utf-8'))>1_000_000:
        raise SafetyError('Link page exceeds one million encoded bytes; reduce the page size')
    return result
