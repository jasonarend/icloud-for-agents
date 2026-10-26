"""Concrete scoped Notes changes with exact previews and native-content guards."""
import hashlib
import json
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .core import SafetyError, digest
from .native_access import require_write
from .native_once import planning_access
from .native_read import NativeReader, native_config
from .notes_content import project_html
from .native_protocol import mutation_request


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
    operations={'create_note','create_folder','append_note','update_note','move_note'}
    def __init__(self):self.reader=NativeReader('notes')

    @property
    def backend_id(self):
        config=native_config('notes');helper=config.get('helper_path')
        if not helper:raise SafetyError('Configure the signed native helper before Notes writes')
        path=Path(helper)
        from .native_app import validate_bundle
        bundle=validate_bundle(path);resources=bundle/'Contents/Resources'
        digest=hashlib.sha256()
        for file in (path,bundle/'Contents/Info.plist',resources/'notes.js',resources/'notes-write.js'):
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

    def private_folder(self,folder_id,source_id):
        initial=None;ancestors=[];seen=set();current=folder_id
        for _ in range(32):
            if current in seen:raise SafetyError('Cyclic Notes folder ancestry')
            seen.add(current)
            folder=self.reader.read('folder_get',folder_id=current)['folder']
            if folder.get('id')!=current or folder.get('source_id')!=source_id or folder.get('shared') is not False:
                raise SafetyError('The exact folder and every ancestor must be unshared in the selected source')
            if initial is None:initial=dict(folder)
            else:ancestors.append(folder)
            parent=folder.get('parent_id')
            if parent==source_id:
                if ancestors:initial['private_ancestors']=ancestors
                return initial
            if not isinstance(parent,str) or not parent:raise SafetyError('Notes folder ancestry is unavailable')
            current=parent
        raise SafetyError('Notes folder ancestry exceeds the verified bound')

    def note_snapshot(self,item,*,rewrite=True):
        folder=self.private_folder(item['folder_id'],item['source_id'])
        note=self.reader.read('get',folder_id=item['folder_id'],id=item['id'])['item']
        if (folder.get('source_id')!=item['source_id'] or folder.get('shared') is not False
                or note.get('id')!=item['id'] or note.get('folder_id')!=item['folder_id']
                or note.get('source_id')!=item['source_id'] or note.get('locked') is not False
                or note.get('shared') is not False or note.get('truncated') is not False
                or not isinstance(note.get('modified'),str)):
            raise SafetyError('Editing requires one saved, unlocked, unshared note in the selected folder')
        attachments=self.reader.read('attachments',folder_id=item['folder_id'],id=item['id'],limit=1 if rewrite else 100,offset=0)
        if (rewrite and attachments.get('attachments')!=[]) or attachments.get('done') is not True or attachments.get('locked'):
            raise SafetyError('Existing native attachments cannot be rewritten by this adapter')
        if rewrite:
            try:
                # Refuse unsupported exported structures/attributes. Native objects,
                # checklists and tables need their own preservation-capable route.
                parser=SafeHTML();parser.feed(note['html']);parser.close()
                projection=project_html(note['html'])
                if projection['tables'] or not projection['text_projection_complete']:
                    raise SafetyError('Existing native objects need a preservation-capable edit route')
            except ValueError:raise SafetyError('Existing Notes HTML cannot be safely edited') from None
        return {'folder':folder,'note':note,'attachments':attachments['attachments']}

    def move_snapshot(self,item):
        before=self.note_snapshot(item,rewrite=False)
        destination=self.private_folder(item['fields']['destination_folder_id'],item['source_id'])
        if destination.get('source_id')!=item['source_id'] or destination.get('shared') is not False:
            raise SafetyError('Move destination must be an unshared folder in the same source')
        before['destination']=destination
        return before

    def prepare_move(self,change):
        if set(change)-{'operation','source_id','folder_id','id','fields'}:
            raise SafetyError('Unknown move fields')
        if change.get('source_id') not in planning_access('notes')['source_ids']:raise SafetyError('Select an exact source')
        fields=change.get('fields')
        if not isinstance(fields,dict) or set(fields)!={'destination_folder_id'}:
            raise SafetyError('A move requires one exact destination folder')
        for value in (change.get('id'),change.get('folder_id'),fields['destination_folder_id']):
            if not isinstance(value,str) or not 1<=len(value)<=1000:raise SafetyError('Use exact note and folder IDs')
        if change['folder_id']==fields['destination_folder_id']:raise SafetyError('Note is already in the requested folder')
        item={key:change[key] for key in ('operation','source_id','folder_id','id')}
        item['fields']=dict(fields);item['before']=self.move_snapshot(item)
        item['fields']['title']=item['before']['note']['title']
        item.update(fidelity_verified=True,effects={'existing_content':'Move the existing native object; no body or attachment rewrite',
            'sharing':'same source and unshared note/folders only','verification':'exact exported HTML, text, creation metadata and exposed attachment identities',
            'native_metadata':'unexposed native metadata and cloud sync remain unverified'})
        return item

    def verify_move(self,item,outcome):
        destination=item['fields']['destination_folder_id']
        if outcome.get('id')!=item['id'] or outcome.get('folder_id')!=destination:return False
        self.private_folder(destination,item['source_id'])
        note=self.reader.read('get',folder_id=destination,id=item['id'])['item'];before=item['before']['note']
        if note.get('source_id')!=item['source_id'] or note.get('folder_id')!=destination:return False
        if any(note.get(key)!=before.get(key) for key in ('id','title','html','text','created','locked','shared')):return False
        if note.get('locked') is not False or note.get('shared') is not False or note.get('truncated'):return False
        attachments=self.reader.read('attachments',folder_id=destination,id=item['id'],limit=100,offset=0)
        if attachments.get('done') is not True or attachments.get('attachments')!=item['before']['attachments']:return False
        return self.reader.read('contains',folder_id=item['folder_id'],id=item['id']).get('exists') is False

    def prepare_existing(self,change):
        if set(change)-{'operation','source_id','folder_id','id','fields'}:
            raise SafetyError('Unknown existing-note change fields')
        source=change.get('source_id')
        if source not in planning_access('notes')['source_ids']:raise SafetyError('Select an exact Notes source')
        for key in ('folder_id','id'):
            if not isinstance(change.get(key),str) or not 1<=len(change[key])<=1000:
                raise SafetyError('Select an exact Notes folder and note ID')
        item={key:change[key] for key in ('operation','source_id','folder_id','id')}
        before=self.note_snapshot(item);fields=change.get('fields')
        if item['operation']=='append_note':
            if not isinstance(fields,dict) or set(fields)-{'body','format'} or not fields.get('body'):
                raise SafetyError('Append requires nonempty body and optional format')
            content=prepare_content({'title':before['note']['title'],**fields})
            html=before['note']['html']+content['html'];title=before['note']['title']
            effect='Append the supplied body; retain supported existing text, links and basic formatting'
        else:
            if not isinstance(fields,dict) or not {'title','body'}<=set(fields):
                raise SafetyError('Replacement requires an explicit complete title and body')
            content=prepare_content(fields);title=content['title']
            html='<div>'+escape(title)+'</div>'+content['html']
            effect='Replace this entire supported title/body with the explicit supplied content'
        if len(html.encode())>30000:raise SafetyError('Combined Notes body exceeds the native write limit')
        item.update(fields={'title':title,'html':html,'expected_text':project_html(html)['text']},before=before,
                    fidelity_verified=True,effects={'existing_content':effect,'native_objects':'Existing attachments, tables, checklists and unknown structures refused',
                    'sharing':'none','sync_status':'unconfirmed'})
        return item

    def prepare(self,change):
        if isinstance(change,dict) and change.get('operation')=='move_note':return self.prepare_move(change)
        if isinstance(change,dict) and change.get('operation') in {'append_note','update_note'}:
            return self.prepare_existing(change)
        if not isinstance(change,dict) or set(change)-{'operation','source_id','folder_id','parent_folder_id','fields'}:
            raise SafetyError('Unknown Notes change fields')
        operation=change.get('operation');source=change.get('source_id')
        if operation not in self.operations or source not in planning_access('notes')['source_ids']:
            raise SafetyError('Unselected Notes operation/source')
        fields=change.get('fields')
        if operation!='create_folder' and 'parent_folder_id' in change:raise SafetyError('Parent selection applies only to folder creation')
        if operation=='create_folder':
            if not isinstance(fields,dict) or set(fields)!={'title'} or 'folder_id' in change:
                raise SafetyError('Folder creation requires only a title and optional parent folder')
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
        if operation=='create_folder' and 'parent_folder_id' in change:
            parent=change['parent_folder_id']
            if not isinstance(parent,str) or not 1<=len(parent)<=1000 or parent==source:raise SafetyError('Use a real parent folder ID, or omit it for the source root')
            item['parent_folder_id']=parent
        item['before']=self.snapshot(item)
        if operation=='create_note' and item['before'].get('shared'):
            raise SafetyError('Shared Notes writes need separate collaboration support')
        if operation=='create_folder' and item['before']['matching_titles']>0:
            raise SafetyError('Folder title already exists under this parent')
        if len(json.dumps(item,ensure_ascii=False).encode())>60000:
            raise SafetyError('Prepared note exceeds native request limit')
        return item

    def snapshot(self,item):
        if item['operation']=='move_note':return self.move_snapshot(item)
        if item['operation'] in {'append_note','update_note'}:return self.note_snapshot(item)
        if item['operation']=='create_folder':
            parent=item.get('parent_folder_id',item['source_id'])
            parent_snapshot=self.private_folder(parent,item['source_id']) if 'parent_folder_id' in item else None
            folders=[f for f in self.folders() if f['source_id']==item['source_id'] and f['parent_id']==parent]
            result={'source_id':item['source_id'],'parent_id':parent,'membership_sha256':digest(sorted(folders,key=lambda f:f['id'])),
                    'folder_count':len(folders),'matching_titles':sum(f['title']==item['fields']['title'] for f in folders)}
            if parent_snapshot is not None:result['parent']=parent_snapshot
            return result
        folder=self.private_folder(item['folder_id'],item['source_id'])
        return folder

    def apply(self,item):
        require_write('notes',item)
        config=native_config('notes')
        request=mutation_request('notes',item)
        return self.reader._dispatch(request,config)['result']

    def verify(self,item,outcome):
        if outcome.get('source_id')!=item['source_id'] or not isinstance(outcome.get('id'),str):return False
        if item['operation']=='move_note':return self.verify_move(item,outcome)
        if item['operation']=='create_folder':
            return any(f['id']==outcome['id'] and f['title']==item['fields']['title']
                       and f['source_id']==item['source_id'] and f.get('parent_id')==item.get('parent_folder_id',item['source_id']) for f in self.folders())
        if item['operation'] in {'append_note','update_note'} and outcome['id']!=item['id']:return False
        note=self.reader.read('get',folder_id=item['folder_id'],id=outcome['id'])['item']
        if (note.get('id')!=outcome['id'] or note.get('source_id')!=item['source_id'] or note.get('folder_id')!=item['folder_id']):return False
        if note.get('locked') or note.get('truncated') or note.get('shared',False) or note.get('title')!=item['fields']['title']:return False
        expected=project_html(item['fields']['html'])
        actual=project_html(note.get('html',''))
        if not actual['text_projection_complete']:return False
        if actual['tables']!=expected['tables'] or actual['links']!=expected['links']:return False
        expected_text=expected['text']
        if item['operation'] in {'append_note','update_note'} and note.get('created')!=item['before']['note'].get('created'):return False
        if item['operation']=='append_note' and not note.get('html','').startswith(item['before']['note']['html'].rstrip()):return False
        if item['operation'] in {'append_note','update_note'}:return actual['text']==expected_text
        return actual['text'] in {expected_text,' '.join((item['fields']['title']+' '+expected_text).split())}
