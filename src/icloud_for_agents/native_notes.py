"""Concrete scoped Notes changes with exact previews and native-content guards."""
import json
from html import escape
from html.parser import HTMLParser

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
    operations={'create_note','create_folder','append_note','prepend_note','replace_text','update_note','move_note','rename_folder'}
    def __init__(self):self.reader=NativeReader('notes')

    @property
    def backend_id(self):
        config=native_config('notes');helper=config.get('helper_path')
        if not helper:raise SafetyError('Configure the signed native helper before Notes writes')
        from .native_app import bundle_fingerprint
        identity='notes-creation-v1:'+bundle_fingerprint(helper)
        if config.get('metadata_backend')=='notes-store-readonly':
            from .native_note_store import backend_suffix
            identity+=':'+backend_suffix()
        if config.get('folder_write_backend')=='notes-shared':identity+=':notes-shared-folder-v1'
        return identity

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
        initial=None;ancestors=[];seen=set();current=folder_id;batch=None
        for _ in range(32):
            if current in seen:raise SafetyError('Cyclic Notes folder ancestry')
            seen.add(current)
            if batch is None:
                response=self.reader.read('folder_get',folder_id=current);folder=response['folder']
                if 'ancestors' in response:
                    values=response['ancestors']
                    if (not isinstance(values,list) or len(values)>31 or any(not isinstance(v,dict) or not isinstance(v.get('id'),str) for v in values)
                            or len({v['id'] for v in values})!=len(values) or any(v['id']==folder_id for v in values)):
                        raise SafetyError('Notes ancestor batch is invalid or incomplete')
                    batch={v['id']:v for v in values}
            else:
                if current not in batch:raise SafetyError('Notes ancestor batch does not cover the physical parent chain')
                folder=batch[current]
            if folder.get('id')!=current or folder.get('source_id')!=source_id or folder.get('shared') is not False:
                raise SafetyError('The exact folder and every ancestor must be unshared in the selected source')
            if initial is None:initial=dict(folder)
            else:ancestors.append(folder)
            parent=folder.get('parent_id')
            if parent==source_id:
                if batch is not None and set(batch)!=seen-{folder_id}:raise SafetyError('Notes ancestor batch contains unrelated folders')
                if ancestors:initial['private_ancestors']=ancestors
                return initial
            if not isinstance(parent,str) or not parent:raise SafetyError('Notes folder ancestry is unavailable')
            current=parent
        raise SafetyError('Notes folder ancestry exceeds the verified bound')

    def folder_rename_snapshot(self,item):
        folder=self.private_folder(item['folder_id'],item['source_id'])
        inventory=self.reader.read('folder_inventory',folder_id=item['folder_id'])
        values=inventory.get('folders');notes=inventory.get('notes');siblings=inventory.get('siblings')
        if (not isinstance(values,list) or not 1<=len(values)<=100 or not isinstance(notes,list) or len(notes)>200
                or not isinstance(siblings,list) or len(siblings)>10000
                or any(not isinstance(f,dict) or set(f)!={'id','title','source_id','parent_id','shared','is_default'} for f in values)
                or any(not isinstance(n,dict) or set(n)!={'id','folder_id'} for n in notes)
                or any(not isinstance(f,dict) or set(f)!={'id','title'} for f in siblings)):
            raise SafetyError('Complete bounded folder subtree and note membership required')
        for row in values+notes+siblings:
            for key,value in row.items():
                if key in {'id','parent_id','source_id','folder_id','title'} and (not isinstance(value,str) or not 1<=len(value)<=1000):
                    raise SafetyError('Invalid native inventory identity or title')
        identities={f['id'] for f in values}
        if (len(identities)!=len(values) or len({n['id'] for n in notes})!=len(notes) or len({f['id'] for f in siblings})!=len(siblings)
                or item['folder_id'] in {f['id'] for f in siblings} or any(n['folder_id'] not in identities for n in notes)):
            raise SafetyError('Native inventory has repeated or unrelated identities')
        parents={f['id']:f['parent_id'] for f in values}
        for identity in identities-{item['folder_id']}:
            current=identity;seen=set()
            while current!=item['folder_id']:
                if current in seen or current not in parents:raise SafetyError('Inventory subtree is disconnected or cyclic')
                seen.add(current);current=parents[current]
        roots=[f for f in values if f.get('id')==item['folder_id']]
        if len(roots)!=1 or any(roots[0].get(k)!=folder.get(k) for k in ('id','title','source_id','parent_id','shared')):
            raise SafetyError('Folder inventory and physical ancestry disagree')
        if any(f.get('source_id')!=item['source_id'] or f.get('shared') is not False or f.get('is_default') is not False for f in values):
            raise SafetyError('Renaming requires an entirely private subtree without a default folder')
        if any(f.get('title')==item['fields']['title'] for f in siblings):raise SafetyError('A sibling already has the requested title')
        roles=[]
        for child in values:
            role=self.reader.read('folder_role',folder_id=child['id'])
            if (role.get('folder_id')!=child['id'] or role.get('source_id')!=item['source_id'] or (role.get('title')!=child['title'] and not (child['id']==item['folder_id'] and item.get('rename_title_repair') is True))
                    or role.get('storage_kind')!='regular' or role.get('marked_for_deletion') is not False or role.get('has_smart_query') is not False):
                raise SafetyError('Unknown, stale, special, deleted or smart folder role; no rename')
            roles.append(role)
        return {'folder':folder,'inventory':{k:inventory[k] for k in ('folders','notes','siblings')},'roles':roles}

    def prepare_folder_rename(self,change):
        if set(change)!={'operation','source_id','folder_id','fields'} or change['source_id'] not in planning_access('notes')['source_ids']:
            raise SafetyError('Select an exact source and folder for renaming')
        fields=change['fields']
        if not isinstance(fields,dict) or set(fields)!={'title'}:raise SafetyError('Folder rename changes only its title')
        if native_config('notes').get('folder_write_backend')!='notes-shared':raise SafetyError('Folder rename requires the explicitly configured native model writer and independent store verification')
        title=prepare_content(fields)['title']
        if not isinstance(change['folder_id'],str) or not 1<=len(change['folder_id'])<=1000:raise SafetyError('Select a saved exact folder ID')
        item={'operation':'rename_folder','source_id':change['source_id'],'folder_id':change['folder_id'],'id':change['folder_id'],'fields':{'title':title},
              'writer_backend':'notes-shared','fidelity_verified':True,'effects':{'change':'Rename the existing native folder only; no body, attachment, move, sharing or deletion operation',
              'verification':'Complete bounded native folder/note membership and independently selected storage roles',
              'native_metadata':'Unexposed native note metadata and cloud/device rendering remain unverified'}}
        item['before']=self.folder_rename_snapshot(item)
        if item['before']['folder']['title']==title:raise SafetyError('Folder already has the requested title')
        return item

    def prepare_folder_rename_repair(self,original,target):
        from .native_once import planning
        if original.get('operation')!='rename_folder' or target not in {'requested','before'}:raise SafetyError('Exact known folder-title correction required')
        writer=native_config('notes').get('folder_write_backend','scripting')
        if writer!='notes-shared' and target!='before':raise SafetyError('Scripting correction can only restore the already-persisted original title')
        title=original['before']['folder']['title'] if target=='before' else original['fields']['title']
        item={k:original[k] for k in ('operation','source_id','folder_id','id')};item.update(fields={'title':title},writer_backend=writer,legacy_title_restore=writer=='scripting',rename_title_repair=True,fidelity_verified=True,
            effects={'correction':'Restore the original name' if target=='before' else 'Persist the originally requested name','native_content':'No note body, membership, attachment, sharing or deletion change'})
        item['before']=self.folder_rename_snapshot(item)
        allowed={original['before']['folder']['title'],original['fields']['title']}
        if item['before']['folder']['title'] not in allowed or any(r['title'] not in allowed for r in item['before']['roles'] if r['folder_id']==item['id']):raise SafetyError('Unrelated external title change requires separate review')
        baseline=original['before'];current=item['before']
        expected=dict(baseline['folder'],title=current['folder']['title'])
        if expected!=current['folder']:raise SafetyError('Folder permissions or ancestry changed; no corrective overwrite')
        inventory={k:[dict(row) for row in values] for k,values in baseline['inventory'].items()}
        for row in inventory['folders']:
            if row['id']==item['id']:row['title']=current['folder']['title']
        if inventory!=current['inventory']:raise SafetyError('Folder membership or siblings changed; no corrective overwrite')
        for prior,after in zip(baseline['roles'],current['roles']):
            expected=dict(prior,revision=after['revision'])
            if prior['folder_id']==item['id']:expected['title']=after['title']
            if expected!=after or after['revision']<prior['revision']:raise SafetyError('Folder role changed; no corrective overwrite')
        return item

    def verify_folder_rename(self,item,outcome):
        if outcome.get('id')!=item['id']:return False
        actual=self.folder_rename_snapshot(dict(item,rename_title_repair=False));before=item['before'];expected=dict(before['folder'],title=item['fields']['title'])
        if actual['folder']!=expected:return False
        inventory={k:[dict(row) for row in values] for k,values in before['inventory'].items()}
        for row in inventory['folders']:
            if row['id']==item['id']:row['title']=item['fields']['title']
        if inventory!=actual['inventory'] or len(actual['roles'])!=len(before['roles']):return False
        for prior,after in zip(before['roles'],actual['roles']):
            wanted=dict(prior)
            if prior['folder_id']==item['id']:wanted['title']=item['fields']['title']
            if type(after.get('revision')) is not int or after['revision']<prior['revision']:return False
            wanted['revision']=after['revision']
            if after!=wanted:return False
        return True

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
        # Notes scripting does not expose a trustworthy special-folder role.
        # Recheck old plans as well as fresh proposals until a safe route exists.
        raise SafetyError('Notes moves are unavailable until special folder roles can be verified before dispatch')

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
        if item['operation'] in {'append_note','prepend_note'}:
            if not isinstance(fields,dict) or set(fields)-{'body','format'} or not fields.get('body'):
                raise SafetyError('Append requires nonempty body and optional format')
            content=prepare_content({'title':before['note']['title'],**fields})
            title=before['note']['title']
            if item['operation']=='append_note':
                html=before['note']['html']+content['html']
                effect='Append the supplied body; retain supported existing text, links and basic formatting'
            else:
                from .notes_edit import split_title
                prefix,body=split_title(before['note']['html'],title)
                html=prefix+content['html']+body
                effect='Insert the supplied body immediately after the existing title; preserve original exported body bytes'
        elif item['operation']=='replace_text':
            from .notes_edit import split_title,replace_text
            title=before['note']['title'];prefix,body=split_title(before['note']['html'],title)
            html=prefix+replace_text(body,fields)
            effect='Replace exactly the reviewed literal text-node matches in the body; preserve the title and every other exported HTML byte'
        else:
            if not isinstance(fields,dict) or not {'title','body'}<=set(fields):
                raise SafetyError('Replacement requires an explicit complete title and body')
            content=prepare_content(fields);title=content['title']
            html='<div>'+escape(title)+'</div>'+content['html']
            effect='Replace this entire supported title/body with the explicit supplied content'
        if item['operation']=='replace_text':item['requested_edit']=dict(fields)
        if len(html.encode())>30000:raise SafetyError('Combined Notes body exceeds the native write limit')
        item.update(fields={'title':title,'html':html,'expected_text':project_html(html)['text']},before=before,
                    fidelity_verified=True,effects={'existing_content':effect,'native_objects':'Existing attachments, tables, checklists and unknown structures refused',
                    'sharing':'none','sync_status':'unconfirmed',
                    'folder_role':'Unverified by scripting; Recently Deleted is not a supported edit target.'})
        return item

    def prepare(self,change):
        if isinstance(change,dict) and change.get('operation')=='rename_folder':return self.prepare_folder_rename(change)
        if isinstance(change,dict) and change.get('operation')=='move_note':return self.prepare_move(change)
        if isinstance(change,dict) and change.get('operation') in {'append_note','prepend_note','replace_text','update_note'}:
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
              'rich_native_features':'not certified by text readback',
              'folder_role':'Unverified by scripting; Recently Deleted is not a supported creation target.'}}
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
        if item['operation']=='rename_folder':return self.folder_rename_snapshot(item)
        if item['operation']=='move_note':return self.move_snapshot(item)
        if item['operation'] in {'append_note','prepend_note','replace_text','update_note'}:return self.note_snapshot(item)
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
        if item['operation']=='rename_folder':return self.verify_folder_rename(item,outcome)
        if item['operation']=='move_note':return self.verify_move(item,outcome)
        if item['operation']=='create_folder':
            return any(f['id']==outcome['id'] and f['title']==item['fields']['title']
                       and f['source_id']==item['source_id'] and f.get('parent_id')==item.get('parent_folder_id',item['source_id']) for f in self.folders())
        if item['operation'] in {'append_note','prepend_note','replace_text','update_note'} and outcome['id']!=item['id']:return False
        note=self.reader.read('get',folder_id=item['folder_id'],id=outcome['id'])['item']
        if (note.get('id')!=outcome['id'] or note.get('source_id')!=item['source_id'] or note.get('folder_id')!=item['folder_id']):return False
        if note.get('locked') or note.get('truncated') or note.get('shared',False) or note.get('title')!=item['fields']['title']:return False
        expected=project_html(item['fields']['html'])
        actual=project_html(note.get('html',''))
        if not actual['text_projection_complete']:return False
        if actual['tables']!=expected['tables'] or actual['links']!=expected['links']:return False
        expected_text=expected['text']
        if item['operation'] in {'append_note','prepend_note','replace_text','update_note'} and note.get('created')!=item['before']['note'].get('created'):return False
        if item['operation'] in {'prepend_note','replace_text'}:
            from .notes_edit import equivalent_export
            if not equivalent_export(item['fields']['html'],note.get('html'),empty_paragraph_placeholder=item['operation']=='replace_text' and item.get('requested_edit',{}).get('replacement')==''):return False
        if item['operation']=='append_note' and not note.get('html','').startswith(item['before']['note']['html'].rstrip()):return False
        if item['operation'] in {'append_note','prepend_note','replace_text','update_note'}:return actual['text']==expected_text
        return actual['text'] in {expected_text,' '.join((item['fields']['title']+' '+expected_text).split())}
