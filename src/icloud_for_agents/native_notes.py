"""Concrete approved Notes creation adapter. No existing-note rewrites or deletes.

Not registered for live writes until read-only acceptance and owner enablement.
"""
import hashlib
import json
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .core import SafetyError, digest
from .native_access import require_write
from .native_read import NativeReader, native_config
from .notes_content import project_html


class SafeHTML(HTMLParser):
    allowed={'div','p','br','strong','b','em','i','u','s','h1','h2','h3','h4','ul','ol','li',
             'table','thead','tbody','tr','th','td','blockquote','pre','code','a'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag not in self.allowed:
            raise SafetyError('Unsupported Notes HTML element; no silent conversion')
        for key,value in attrs:
            if tag!='a' or key!='href' or not value or not value.startswith(('https://','http://')):
                raise SafetyError('Unsupported Notes HTML attribute or URL')
        if tag in {'br','div','p','li','tr','th','td','h1','h2','h3','h4'}:self.text.append(' ')
    def handle_endtag(self,tag):
        if tag not in self.allowed:raise SafetyError('Unsupported Notes HTML element')
        if tag in {'div','p','li','tr','th','td','h1','h2','h3','h4','ul','ol','table','thead','tbody','blockquote','pre'}:
            self.text.append(' ')
    def handle_data(self,data):self.text.append(data)
    def handle_comment(self,data):raise SafetyError('Notes HTML comments are not supported')
    def handle_pi(self,data):raise SafetyError("Notes HTML processing instructions are not supported")
    def unknown_decl(self,data):raise SafetyError("Notes HTML declarations are not supported")
    def handle_decl(self,decl):raise SafetyError('Notes HTML declarations are not supported')


def prepare_content(fields):
    if not isinstance(fields,dict) or set(fields)-{'title','body','format'}:
        raise SafetyError('Use exact title/body/format fields')
    title,body=fields.get('title'),fields.get('body','')
    if (not isinstance(title,str) or not title.strip() or len(title)>1000 or '\n' in title
            or any(ord(c)<32 for c in title) or not isinstance(body,str) or len(body.encode())>20000 or '\x00' in body):
        raise SafetyError('Invalid bounded Notes content')
    format=fields.get('format','text')
    if format not in {'text','html'}:raise SafetyError('Use text or explicit supported HTML')
    html=('<div>'+escape(body).replace('\n','<br>')+'</div>') if format=='text' else body
    if len(html.encode())>30000:raise SafetyError('Rendered Notes HTML exceeds native input limit')
    parser=SafeHTML();parser.feed(html);parser.close()
    return {'title':title,'html':html,'expected_text':' '.join(''.join(parser.text).split())}


class NotesCreateAdapter:
    operations={'create_note','create_folder'}
    def __init__(self):self.reader=NativeReader('notes')

    @property
    def backend_id(self):
        config=native_config('notes');helper=config.get('helper_path')
        if not helper:raise SafetyError('Configure the signed native helper before Notes writes')
        path=Path(helper);resources=path.parent.parent/'Resources'
        digest=hashlib.sha256()
        for file in (path,resources/'notes.js',resources/'notes-write.js'):
            if not file.is_file() or file.stat().st_size>50_000_000:
                raise SafetyError('Invalid Notes helper assets')
            digest.update(file.read_bytes())
        return 'notes-creation-v1:'+digest.hexdigest()

    def folders(self):
        result=[];offset=0
        while True:
            page=self.reader.read('folders',limit=100,offset=offset)
            result.extend(page['folders'])
            if page.get('done'):return result
            next_offset=page.get('next_offset')
            if type(next_offset) is not int or next_offset<=offset or next_offset>10000:
                raise SafetyError('Folder discovery is incomplete; no write')
            offset=next_offset

    def prepare(self,change):
        if not isinstance(change,dict) or set(change)-{'operation','source_id','folder_id','fields'}:
            raise SafetyError('Unknown Notes change fields')
        operation=change.get('operation');source=change.get('source_id')
        if operation not in self.operations or source not in require_write('notes')['source_ids']:
            raise SafetyError('Unselected Notes operation/source')
        fields=change.get('fields')
        if operation=='create_folder':
            if not isinstance(fields,dict) or set(fields)!={'title'} or 'folder_id' in change:
                raise SafetyError('Root folder creation requires only a title')
            prepared=prepare_content({'title':fields['title']})
            fields={'title':prepared['title']}
        else:
            if not isinstance(change.get('folder_id'),str) or not change['folder_id']:
                raise SafetyError('Select an exact Notes folder')
            fields=prepare_content(fields)
        item={'operation':operation,'source_id':source,'fields':fields,
              'fidelity_verified':True,'effects':{'sharing':'none','existing_content':'unchanged',
              'rich_native_features':'not certified by text readback'}}
        if operation=='create_note':item['folder_id']=change['folder_id']
        item['before']=self.snapshot(item)
        if operation=='create_note' and item['before'].get('shared'):
            raise SafetyError('Shared Notes writes need separate collaboration support')
        if operation=='create_folder' and item['before']['matching_titles']>0:
            raise SafetyError('Folder title already exists in this account')
        if len(json.dumps(item,ensure_ascii=False).encode())>60000:
            raise SafetyError('Prepared note exceeds native request limit')
        return item

    def snapshot(self,item):
        if item['operation']=='create_folder':
            folders=[f for f in self.folders() if f['source_id']==item['source_id'] and f['parent_id']==item['source_id']]
            return {'source_id':item['source_id'],'membership_sha256':digest(sorted(folders,key=lambda f:f['id'])),
                    'root_folder_count':len(folders),'matching_titles':sum(f['title']==item['fields']['title'] for f in folders)}
        folder=self.reader.read('folder_get',folder_id=item['folder_id'])['folder']
        if folder['source_id']!=item['source_id']:raise SafetyError('Folder belongs to another Notes source')
        return folder

    def apply(self,item):
        require_write('notes')
        config=native_config('notes')
        request={'protocol_version':1,'service':'notes','action':'apply',
                 'source_ids':[item['source_id']],'change':item}
        return self.reader._dispatch(request,config)['result']

    def verify(self,item,outcome):
        if outcome.get('source_id')!=item['source_id'] or not isinstance(outcome.get('id'),str):return False
        if item['operation']=='create_folder':
            return any(f['id']==outcome['id'] and f['title']==item['fields']['title']
                       and f['source_id']==item['source_id'] and f.get('parent_id')==item['source_id'] for f in self.folders())
        note=self.reader.read('get',folder_id=item['folder_id'],id=outcome['id'])['item']
        if note.get('locked') or note.get('truncated') or note.get('title')!=item['fields']['title']:return False
        expected=project_html(item['fields']['html'])
        actual=project_html(note.get('html',''))
        if not actual['text_projection_complete']:return False
        if actual['tables']!=expected['tables'] or actual['links']!=expected['links']:return False
        expected_text=expected['text']
        return actual['text'] in {expected_text,' '.join((item['fields']['title']+' '+expected_text).split())}
