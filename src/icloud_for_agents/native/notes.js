// Fixed read-only JXA protocol. User data is JSON, never executable script source.
ObjC.import('Foundation');

// Fixed metadata inventory: no note body, attachment or unlock operation.
function notesFolderInventory(account,root) {
    const records=[],notes=[],seenFolders=new Set(),seenNotes=new Set(),source=account.id();
    const defaultID=account.defaultFolder().id();
    function visit(folder,depth) {
        if(depth>=32 || records.length>=100)throw Error('folder_limit');
        const id=folder.id();if(seenFolders.has(id))throw Error('folder_limit');seenFolders.add(id);
        const shared=folder.shared(),parent=folder.container().id(),title=folder.name();
        if(typeof shared!=='boolean' || typeof title!=='string' || title.length>1000)throw Error('folder_metadata_unavailable');
        records.push({id:id,title:title,source_id:source,parent_id:parent,shared:shared,is_default:id===defaultID});
        const ids=folder.notes.id();if(ids.length>200)throw Error('query_limit');
        ids.forEach(noteID=>{
            const note=folder.notes.byId(noteID);if(note.container().id()!==id)return;
            if(seenNotes.has(noteID) || notes.length>=200)throw Error('query_limit');seenNotes.add(noteID);
            notes.push({id:noteID,folder_id:id});
        });
        folder.folders().filter(f=>f.container().id()===id).forEach(f=>visit(f,depth+1));
    }
    visit(root,0);
    const parentID=root.container().id(),parent=parentID===source?account:account.folders.byId(parentID);
    const children=parent.folders();if(children.length>10000)throw Error('folder_limit');
    const siblings=children.filter(f=>f.container().id()===parentID && f.id()!==root.id()).map(f=>({id:f.id(),title:f.name()}));
    const sorted=values=>values.sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
    return {folders:sorted(records),notes:sorted(notes),siblings:sorted(siblings)};
}
function notesMetadataEqual(a,b) {
    if(a===b)return true;
    if(a===null || b===null || typeof a!=='object' || typeof b!=='object' || Array.isArray(a)!==Array.isArray(b))return false;
    if(Array.isArray(a))return a.length===b.length && a.every((value,index)=>notesMetadataEqual(value,b[index]));
    const keys=Object.keys(a).sort(),other=Object.keys(b).sort();
    return keys.length===other.length && keys.every((key,index)=>key===other[index] && notesMetadataEqual(a[key],b[key]));
}

function readRequest() {
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
    if (text.length > 65536) throw Error('invalid_request');
    const req = JSON.parse(text);
    if (req.protocol_version !== 1 || !['sources','folders','default_folder','folder_get','folder_inventory','query','get','text_page','search','attachments','diagnose_note','contains'].includes(req.action)) throw Error('unsupported_action');
    return req;
}
function run() { const req=readRequest(); return readNotes(Application('com.apple.Notes'), req); }
function readNotes(app, req) {
    const accounts = app.accounts();
    if (req.action === 'sources') return JSON.stringify({protocol_version:1, sources: accounts.map(a => ({id:a.id(), title:a.name()}))});
    if (!Array.isArray(req.source_ids) || req.source_ids.length === 0) throw Error('source_scope_required');
    const selected = accounts.filter(a => req.source_ids.includes(a.id()));
    if(req.source_ids.some(id=>!selected.some(a=>a.id()===id)))throw Error('not_found_in_scope');
    if(req.action==='default_folder') {
        const account=selected.find(a=>a.id()===req.source_id);
        if(!account)throw Error('not_found_in_scope');
        const folder=account.defaultFolder(),id=folder.id(),parent=folder.container().id(),title=folder.name(),shared=folder.shared();
        if(typeof id!=='string' || !id || parent!==account.id() || typeof title!=='string' || typeof shared!=='boolean')throw Error('folder_metadata_unavailable');
        return JSON.stringify({protocol_version:1,folder:{id:id,source_id:account.id(),parent_id:parent,title:title.slice(0,1000),shared:shared,role:'default_folder'}});
    }
    const folders = [];let targetPath=[];
    const seen = new Set();
    function walk(parent, source, depth) {
        if (depth > 32 || folders.length > 10000) throw Error('folder_limit');
        parent.folders().forEach(f => {
            const id = f.id();
            if (seen.has(id)) return;
            seen.add(id);
            folders.push({object:f, source:source}); walk(f,source,depth+1);
        });
    }
    if(req.folder_id && !['folders','search'].includes(req.action)) {
        // Bind the object specifier to the selected account. App-global folder
        // lookup can address the wrong account context for a nested object.
        const owners=selected.filter(a=>{const ids=a.folders.id();if(ids.length>10000)throw Error('folder_limit');return ids.includes(req.folder_id);});
        let account,target,fromTree=false;
        if(owners.length===1){account=owners[0];target=account.folders.byId(req.folder_id);}
        else if(owners.length>1)throw Error('not_found_in_scope');
        else {
            selected.forEach(a=>walk(a,a.id(),0));
            const matches=folders.filter(f=>f.object.id()===req.folder_id);
            if(matches.length!==1)throw Error('not_found_in_scope');
            target=matches[0].object;account=selected.find(a=>a.id()===matches[0].source);fromTree=true;
        }
        if(target.id()!==req.folder_id)throw Error('not_found_in_scope');
        let current=target, source=null;const ancestors=new Set();
        for(let depth=0;depth<32;depth++) {
            const id=current.id();if(ancestors.has(id))throw Error('folder_limit');ancestors.add(id);targetPath.push(current);
            const parent=current.container().id();
            if(parent===account.id()) {source=parent;break;}
            if(accounts.some(a=>a.id()===parent))throw Error('not_found_in_scope');
            if(fromTree){const found=folders.filter(f=>f.source===account.id() && f.object.id()===parent);if(found.length!==1)throw Error('not_found_in_scope');current=found[0].object;}
            else current=account.folders.byId(parent);
        }
        if(source===null)throw Error('not_found_in_scope');
        folders.splice(0,folders.length,{object:target,source:source});
    } else selected.forEach(a => walk(a,a.id(),0));
    if (req.action==='search') {
        if(typeof req.text!=='string' || (req.text.length<1 && req.match_scope!=='title') || req.text.length>1000 || !Number.isInteger(req.limit) || req.limit<1 || req.limit>100 || !Number.isInteger(req.offset) || req.offset<0 || req.offset>100000)throw Error('invalid_page');
        if(req.match_scope!==undefined && req.match_scope!=='title')throw Error('invalid_fields');
        if(req.locked!==undefined && typeof req.locked!=='boolean')throw Error('invalid_fields');
        if(req.shared!==undefined && typeof req.shared!=='boolean')throw Error('invalid_fields');
        if(req.has_attachments!==undefined && typeof req.has_attachments!=='boolean')throw Error('invalid_fields');
        const titleOnly=req.match_scope==='title';
        const hasModified=req.modified_from!==undefined || req.modified_before!==undefined;
        const hasCreated=req.created_from!==undefined || req.created_before!==undefined;
        for(const value of [req.modified_from,req.modified_before,req.created_from,req.created_before])if(value!==undefined) {
            if(typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value))throw Error('invalid_fields');
            const date=new Date(value);if(!Number.isFinite(date.getTime()) || date.toISOString()!==value.slice(0,-1)+'.000Z')throw Error('invalid_fields');
        }
        if(req.modified_from!==undefined && req.modified_before!==undefined && req.modified_from>=req.modified_before)throw Error('invalid_fields');
        if(req.created_from!==undefined && req.created_before!==undefined && req.created_from>=req.created_before)throw Error('invalid_fields');
        const refs=[];
        folders.filter(f=>!req.folder_id || f.object.id()===req.folder_id).forEach(f=>{
            f.object.notes.id().forEach(id=>refs.push({id:id,folder:f}));
            if(refs.length>100000)throw Error('query_limit');
        });
        if(req.folder_id && !folders.some(f=>f.object.id()===req.folder_id))throw Error('not_found_in_scope');
        refs.sort((a,b)=>a.id.localeCompare(b.id));
        const needle=req.text.toLowerCase(), items=[], unavailable=[], projections=[];
        const scanLimit=(!titleOnly || req.locked!==undefined || req.shared!==undefined || req.has_attachments!==undefined || hasCreated || hasModified)?10:100;
        let projectionBytes=0;
        let offset=req.offset, scanned=0, lockedSkipped=0, truncatedBodies=0;
        while(offset<refs.length && scanned<scanLimit && items.length+projections.length<req.limit) {
            const ref=refs[offset++];scanned++;
            try {
                const note=ref.folder.object.notes.byId(ref.id);
                if(note.container().id()!==ref.folder.object.id())continue;
                let modified=null, created=null;
                if(hasModified) {
                    const date=note.modificationDate();
                    if(!date || !Number.isFinite(date.getTime())){unavailable.push({id:ref.id,category:'modification_date_unavailable'});continue;}
                    modified=date.toISOString();
                    if((req.modified_from && modified<req.modified_from.slice(0,-1)+'.000Z') || (req.modified_before && modified>=req.modified_before.slice(0,-1)+'.000Z'))continue;
                }
                if(hasCreated) {
                    const date=note.creationDate();
                    if(!date || !Number.isFinite(date.getTime())){unavailable.push({id:ref.id,category:'creation_date_unavailable'});continue;}
                    created=date.toISOString();
                    if((req.created_from && created<req.created_from.slice(0,-1)+'.000Z') || (req.created_before && created>=req.created_before.slice(0,-1)+'.000Z'))continue;
                }
                const title=note.name(), locked=note.passwordProtected();
                if(typeof locked!=='boolean')throw Error('lock_status_unavailable');
                if(req.locked!==undefined && locked!==req.locked)continue;
                let shared;
                if(req.shared!==undefined){shared=note.shared();if(typeof shared!=='boolean')throw Error('sharing_status_unavailable');if(shared!==req.shared)continue;}
                let hasAttachments;
                if(req.has_attachments!==undefined){
                    if(locked){unavailable.push({id:ref.id,category:'locked_note_attachments_unavailable'});continue;}
                    const attachments=note.attachments();
                    if(!Array.isArray(attachments) || attachments.length>1000)throw Error('attachment_inventory_unavailable');
                    hasAttachments=attachments.length>0;if(hasAttachments!==req.has_attachments)continue;
                }
                if(titleOnly) {
                    const boundedTitle=title.slice(0,1000);
                    if(boundedTitle.toLowerCase().includes(needle))items.push({id:ref.id,title:boundedTitle,source_id:ref.folder.source,folder_id:ref.folder.object.id(),locked:locked,excerpt:'',content_complete:false,...(req.shared!==undefined?{shared:shared}:{}),...(req.has_attachments!==undefined?{has_attachments:hasAttachments}:{}),...(hasModified?{modified:modified}:{}),...(hasCreated?{created:created}:{})});
                    continue;
                }
                let body='';
                if(locked)lockedSkipped++;
                else {
                    const full=note.plaintext();if(typeof full!=='string')throw Error('plaintext_unavailable');
                    let cap=Math.min(full.length,5000000);
                    if(cap<full.length && cap>0 && full.charCodeAt(cap-1)>=0xD800 && full.charCodeAt(cap-1)<=0xDBFF && full.charCodeAt(cap)>=0xDC00 && full.charCodeAt(cap)<=0xDFFF)cap--;
                    body=full.slice(0,cap);if(full.length>cap)truncatedBodies++;
                }
                const index=body.toLowerCase().indexOf(needle);
                const excerpt=()=>{
                    if(index<0)return '';
                    let start=Math.max(0,index-80), end=Math.min(body.length,index+240);
                    const high=c=>c>=0xD800 && c<=0xDBFF,low=c=>c>=0xDC00 && c<=0xDFFF;
                    if(start>0 && low(body.charCodeAt(start)) && high(body.charCodeAt(start-1)))start--;
                    if(end<body.length && end>0 && high(body.charCodeAt(end-1)) && low(body.charCodeAt(end)))end++;
                    return body.slice(start,end);
                };
                if(title.toLowerCase().includes(needle) || index>=0)items.push({id:ref.id,title:title.slice(0,1000),
                    source_id:ref.folder.source,folder_id:ref.folder.object.id(),locked:locked,
                    excerpt:excerpt(),content_complete:false,...(req.shared!==undefined?{shared:shared}:{}),...(req.has_attachments!==undefined?{has_attachments:hasAttachments}:{}),...(hasModified?{modified:modified}:{}),...(hasCreated?{created:created}:{})});
                else if(!locked) {
                    const html=note.body();
                    if(html.includes('<table')) {
                        const bounded=html.slice(0,100000);
                        if(projectionBytes+bounded.length>250000)unavailable.push({id:ref.id,category:'table_projection_budget'});
                        else {projectionBytes+=bounded.length;projections.push({id:ref.id,title:title.slice(0,1000),
                            source_id:ref.folder.source,folder_id:ref.folder.object.id(),locked:false,html:bounded,
                            truncated:html.length>100000,...(req.shared!==undefined?{shared:shared}:{}),...(req.has_attachments!==undefined?{has_attachments:hasAttachments}:{}),...(hasModified?{modified:modified}:{}),...(hasCreated?{created:created}:{})});}
                    }
                }
            } catch(_) {unavailable.push({id:ref.id,category:'note_read_unavailable'});}
        }
        return JSON.stringify({protocol_version:1,items:items,table_projection_candidates:projections,done:offset>=refs.length,next_offset:offset,
            scanned:scanned,unavailable:unavailable,locked_bodies_excluded:lockedSkipped,truncated_bodies:truncatedBodies,
            search_coverage:titleOnly?'bounded title only; no note bodies read':'title, bounded plaintext and exported table text; no native OCR/attachment/semantic index',pagination_consistency:'live_offset_may_shift',
            ...(titleOnly?{match_scope:'title'}:{}),
            ...(!titleOnly?{plaintext_search_limit:5000000,plaintext_search_unit:'utf16_code_units'}:{}),
            ...(req.locked!==undefined?{locked_filter:req.locked}:{}),
            ...(req.shared!==undefined?{shared_filter:req.shared}:{}),
            ...(req.has_attachments!==undefined?{attachment_filter:req.has_attachments}:{}),
            ...(hasModified?{modified_filter:{from:req.modified_from||null,before:req.modified_before||null,semantics:'utc_inclusive_from_exclusive_before'}}:{}),
            ...(hasCreated?{created_filter:{from:req.created_from||null,before:req.created_before||null,semantics:'utc_inclusive_from_exclusive_before'}}:{})});
    }
    if (req.action === 'folders') {
        const limit=req.limit===undefined?100:req.limit, offset=req.offset===undefined?0:req.offset;
        if (!Number.isInteger(limit) || limit<1 || limit>100 || !Number.isInteger(offset) || offset<0 || offset>100000) throw Error('invalid_page');
        const sorted=folders.slice().sort((a,b)=>a.object.id().localeCompare(b.object.id()));
        const page=sorted.slice(offset,offset+limit).map(f => ({id:f.object.id(), title:f.object.name().slice(0,1000), source_id:f.source, parent_id:f.object.container().id()}));
        return JSON.stringify({protocol_version:1,folders:page,done:offset+page.length>=sorted.length,next_offset:offset+page.length,pagination_consistency:'live_offset_may_shift'});
    }
    if (!req.folder_id) throw Error('folder_scope_required');
    const matches = folders.filter(f => f.object.id() === req.folder_id);
    if (matches.length !== 1) throw Error('not_found_in_scope');
    const folder = matches[0];
    if(req.action==='folder_inventory')return JSON.stringify(Object.assign({protocol_version:1},notesFolderInventory(selected.find(a=>a.id()===folder.source),folder.object)));
    if (req.action==='folder_get') {
        const metadata=f=>({id:f.id(),title:f.name(),source_id:folder.source,parent_id:f.container().id(),shared:f.shared()});
        return JSON.stringify({protocol_version:1,folder:metadata(folder.object),ancestors:targetPath.slice(1).map(metadata)});
    }
    const ids = folder.object.notes.id();
    if(req.action==='contains')return JSON.stringify({protocol_version:1,exists:ids.includes(req.id) && folder.object.notes.byId(req.id).container().id()===req.folder_id,source_id:folder.source,folder_id:req.folder_id});
    if (req.action === 'get' || req.action==='text_page' || req.action==='attachments' || req.action==='diagnose_note') {
        if (!ids.includes(req.id)) throw Error('not_found_in_scope');
        const n = folder.object.notes.byId(req.id);
        if(n.container().id()!==req.folder_id)throw Error('not_found_in_scope');
        // Locked notes must not be read or implicitly unlocked.
        let locked;
        try { locked = n.passwordProtected(); } catch (_) { throw Error('lock_status_unavailable'); }
        if (locked) {
            if(req.action==='text_page')return JSON.stringify({protocol_version:1,id:req.id,source_id:folder.source,folder_id:req.folder_id,locked:true,content_available:false,data_mutated:false});
            if(req.action==='attachments')return JSON.stringify(Object.assign({protocol_version:1,attachments:[],source_id:folder.source,folder_id:req.folder_id,note_id:req.id,locked:true,content_available:false,done:true,excluded_locked_note:true},req.include_attachment_metadata===true?{metadata_requested:true}:{}));
            return JSON.stringify({protocol_version:1,item:{id:req.id,source_id:folder.source,folder_id:req.folder_id,locked:true,content_available:false}});
        }
        if(req.action==='text_page') {
            if(!Number.isInteger(req.limit) || req.limit<1 || req.limit>10000 || !Number.isInteger(req.offset) || req.offset<0 || req.offset>5000000 || (req.expected_modified!==undefined && (typeof req.expected_modified!=='string' || !req.expected_modified.length || req.expected_modified.length>100)))throw Error('invalid_page');
            const modified=()=>{const date=n.modificationDate();return date?date.toISOString():null;};
            const before=modified();if(req.expected_modified!==undefined && before!==req.expected_modified)return JSON.stringify({protocol_version:1,error:'snapshot_changed'});
            const text=n.plaintext();if(typeof text!=='string')throw Error('read_failed');
            const after=modified();if(before!==after)return JSON.stringify({protocol_version:1,error:'snapshot_changed'});
            let cap=Math.min(text.length,5000000);
            const high=c=>c>=0xD800 && c<=0xDBFF,low=c=>c>=0xDC00 && c<=0xDFFF;
            if(cap<text.length && cap>0 && high(text.charCodeAt(cap-1)) && low(text.charCodeAt(cap)))cap--;
            if(req.offset>cap || (req.offset>0 && req.offset<cap && high(text.charCodeAt(req.offset-1)) && low(text.charCodeAt(req.offset))))return JSON.stringify({protocol_version:1,error:'invalid_page'});
            let end=Math.min(cap,req.offset+req.limit);
            if(end<cap && end>req.offset && high(text.charCodeAt(end-1)) && low(text.charCodeAt(end)))end++;
            return JSON.stringify({protocol_version:1,id:req.id,source_id:folder.source,folder_id:req.folder_id,locked:false,content_available:true,text:text.slice(req.offset,end),offset:req.offset,next_offset:end,done:end>=cap,source_truncated:text.length>cap,content_complete:false,modified:after,pagination_consistency:'live_note_may_change',offset_unit:'utf16_code_units',data_mutated:false});
        }
        if(req.action==='diagnose_note') {
            const fields={};
            ['id','name','passwordProtected','body','plaintext','modificationDate'].forEach(key=>{
                try {const value=n[key]();fields[key]={type:typeof value,is_null:value===null,length:typeof value==='string'?value.length:null,is_date:value instanceof Date};}
                catch(error) {const match=String(error).match(/-\d{3,6}/);fields[key]={error_code:match?Number(match[0]):null};}
            });
            const attachmentDiagnostics=[];
            try {
                const all=n.attachments();
                all.slice(0,10).forEach(a=>{
                    const item={};['id','name','contentIdentifier'].forEach(key=>{
                        try {const value=a[key]();item[key]={type:typeof value,is_null:value===null,length:typeof value==='string'?value.length:null};}
                        catch(error) {const match=String(error).match(/-\d{3,6}/);item[key]={error_code:match?Number(match[0]):null};}
                    });attachmentDiagnostics.push(item);
                });
                fields.attachment_count={count:all.length};
            } catch(error) {const match=String(error).match(/-\d{3,6}/);fields.attachment_count={error_code:match?Number(match[0]):null};}
            return JSON.stringify({protocol_version:1,field_diagnostics:fields,attachment_diagnostics:attachmentDiagnostics});
        }
        if(req.action==='attachments') {
            if(!Number.isInteger(req.limit) || req.limit<1 || req.limit>100 || !Number.isInteger(req.offset) || req.offset<0 || req.offset>100000)throw Error('invalid_page');
            if(req.include_attachment_metadata!==undefined && typeof req.include_attachment_metadata!=='boolean')throw Error('invalid_fields');
            const all=n.attachments().slice().sort((a,b)=>a.id().localeCompare(b.id()));
            const page=all.slice(req.offset,req.offset+req.limit).map(a=>{
                const name=a.name(),row={id:a.id(),name:typeof name==='string'?name.slice(0,1000):null,content_identifier:a.contentIdentifier(),note_id:req.id};
                if(req.include_attachment_metadata===true){
                    const observe=(read,kind)=>{try {let value=read();if(value===undefined)value=null;if(value!==null){if(kind==='date'){if(!(value instanceof Date)||isNaN(value.getTime()))throw Error('bad_date');value=value.toISOString();}else if(kind==='bool'){if(typeof value!=='boolean')throw Error('bad_bool');}else if(typeof value!=='string'||value.length>4096)throw Error('bad_string');}return {status:'available',value:value};}catch(_){return {status:'unavailable',value:null};}};
                    row.native_metadata={created:observe(()=>a.creationDate(),'date'),modified:observe(()=>a.modificationDate(),'date'),url:observe(()=>a.url(),'text'),shared:observe(()=>a.shared(),'bool')};
                }
                return row;
            });
            return JSON.stringify(Object.assign({protocol_version:1,attachments:page,source_id:folder.source,folder_id:req.folder_id,note_id:req.id,done:req.offset+page.length>=all.length,
                next_offset:req.offset+page.length,content_downloaded:false,metadata_complete:false},req.include_attachment_metadata===true?{metadata_requested:true}:{}));
        }
        const body = n.body();
        const plain = n.plaintext();
        return JSON.stringify({protocol_version:1,item:{id:n.id(),source_id:folder.source,folder_id:req.folder_id,
            title:n.name().slice(0,1000),html:body.slice(0,100000),text:plain.slice(0,100000),
            truncated:body.length>100000 || plain.length>100000 || n.name().length>1000,created:n.creationDate()?n.creationDate().toISOString():null,modified:n.modificationDate() ? n.modificationDate().toISOString() : null,
            locked:false,shared:n.shared(),content_complete:false,omitted_features:['native_objects','attachments','collaboration_metadata']}});
    }
    if (!Number.isInteger(req.limit) || req.limit < 1 || req.limit > 100 || !Number.isInteger(req.offset) || req.offset < 0 || req.offset > 100000) throw Error('invalid_page');
    const sorted = ids.slice().sort();
    const scanned=sorted.slice(req.offset,req.offset+req.limit);
    const page = scanned.map(id => {
        const n=folder.object.notes.byId(id);
        if(n.container().id()!==req.folder_id)return null;
        return {id:id,title:n.name().slice(0,1000),source_id:folder.source,folder_id:req.folder_id};
    }).filter(n=>n!==null);
    return JSON.stringify({protocol_version:1,items:page,done:req.offset+scanned.length>=sorted.length,
        next_offset:req.offset+scanned.length,pagination_consistency:'live_offset_may_shift'});
}
