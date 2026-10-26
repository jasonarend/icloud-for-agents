// Fixed read-only JXA protocol. User data is JSON, never executable script source.
ObjC.import('Foundation');
function readRequest() {
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
    if (text.length > 65536) throw Error('invalid_request');
    const req = JSON.parse(text);
    if (req.protocol_version !== 1 || !['sources','folders','folder_get','query','get','search','attachments','diagnose_note'].includes(req.action)) throw Error('unsupported_action');
    return req;
}
function run() { const req=readRequest(); return readNotes(Application('Notes'), req); }
function readNotes(app, req) {
    const accounts = app.accounts();
    if (req.action === 'sources') return JSON.stringify({protocol_version:1, sources: accounts.map(a => ({id:a.id(), title:a.name()}))});
    if (!Array.isArray(req.source_ids) || req.source_ids.length === 0) throw Error('source_scope_required');
    const selected = accounts.filter(a => req.source_ids.includes(a.id()));
    if(req.source_ids.some(id=>!selected.some(a=>a.id()===id)))throw Error('not_found_in_scope');
    const folders = [];
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
    selected.forEach(a => walk(a,a.id(),0));
    if (req.action==='search') {
        if(typeof req.text!=='string' || req.text.length<1 || req.text.length>1000 || !Number.isInteger(req.limit) || req.limit<1 || req.limit>100 || !Number.isInteger(req.offset) || req.offset<0 || req.offset>100000)throw Error('invalid_page');
        const refs=[];
        folders.filter(f=>!req.folder_id || f.object.id()===req.folder_id).forEach(f=>{
            f.object.notes.id().forEach(id=>refs.push({id:id,folder:f}));
            if(refs.length>100000)throw Error('query_limit');
        });
        if(req.folder_id && !folders.some(f=>f.object.id()===req.folder_id))throw Error('not_found_in_scope');
        refs.sort((a,b)=>a.id.localeCompare(b.id));
        const needle=req.text.toLowerCase(), items=[], unavailable=[], projections=[];
        let projectionBytes=0;
        let offset=req.offset, scanned=0, lockedSkipped=0, truncatedBodies=0;
        while(offset<refs.length && scanned<100 && items.length+projections.length<req.limit) {
            const ref=refs[offset++];scanned++;
            try {
                const note=ref.folder.object.notes.byId(ref.id), title=note.name(), locked=note.passwordProtected();
                let body='';
                if(locked)lockedSkipped++;
                else {const full=note.plaintext();body=full.slice(0,100000);if(full.length>100000)truncatedBodies++;}
                const index=body.toLowerCase().indexOf(needle);
                if(title.toLowerCase().includes(needle) || index>=0)items.push({id:ref.id,title:title.slice(0,1000),
                    source_id:ref.folder.source,folder_id:ref.folder.object.id(),locked:locked,
                    excerpt:index>=0?body.slice(Math.max(0,index-80),index+240):'',content_complete:false});
                else if(!locked) {
                    const html=note.body();
                    if(html.includes('<table')) {
                        const bounded=html.slice(0,100000);
                        if(projectionBytes+bounded.length>250000)unavailable.push({id:ref.id,category:'table_projection_budget'});
                        else {projectionBytes+=bounded.length;projections.push({id:ref.id,title:title.slice(0,1000),
                            source_id:ref.folder.source,folder_id:ref.folder.object.id(),html:bounded,
                            truncated:html.length>100000});}
                    }
                }
            } catch(_) {unavailable.push({id:ref.id,category:'note_read_unavailable'});}
        }
        return JSON.stringify({protocol_version:1,items:items,table_projection_candidates:projections,done:offset>=refs.length,next_offset:offset,
            scanned:scanned,unavailable:unavailable,locked_bodies_excluded:lockedSkipped,truncated_bodies:truncatedBodies,
            search_coverage:'title, bounded plaintext and exported table text; no native OCR/attachment/semantic index',pagination_consistency:'live_offset_may_shift'});
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
    if (req.action==='folder_get') return JSON.stringify({protocol_version:1,folder:{id:folder.object.id(),title:folder.object.name(),source_id:folder.source,parent_id:folder.object.container().id(),shared:folder.object.shared()}});
    const ids = folder.object.notes.id();
    if (req.action === 'get' || req.action==='attachments' || req.action==='diagnose_note') {
        if (!ids.includes(req.id)) throw Error('not_found_in_scope');
        const n = folder.object.notes.byId(req.id);
        // Locked notes must not be read or implicitly unlocked.
        let locked;
        try { locked = n.passwordProtected(); } catch (_) { throw Error('lock_status_unavailable'); }
        if (locked) {
            if(req.action==='attachments')return JSON.stringify({protocol_version:1,attachments:[],locked:true,content_available:false,done:true,excluded_locked_note:true});
            return JSON.stringify({protocol_version:1,item:{id:req.id,locked:true,content_available:false}});
        }
        if(req.action==='diagnose_note') {
            const fields={};
            ['id','name','passwordProtected','body','plaintext','modificationDate'].forEach(key=>{
                try {const value=n[key]();fields[key]={type:typeof value,is_null:value===null,length:typeof value==='string'?value.length:null,is_date:value instanceof Date};}
                catch(error) {const match=String(error).match(/-\d{3,6}/);fields[key]={error_code:match?Number(match[0]):null};}
            });
            return JSON.stringify({protocol_version:1,field_diagnostics:fields});
        }
        if(req.action==='attachments') {
            if(!Number.isInteger(req.limit) || req.limit<1 || req.limit>100 || !Number.isInteger(req.offset) || req.offset<0 || req.offset>100000)throw Error('invalid_page');
            const all=n.attachments().slice().sort((a,b)=>a.id().localeCompare(b.id()));
            const page=all.slice(req.offset,req.offset+req.limit).map(a=>({id:a.id(),name:a.name().slice(0,1000),content_identifier:a.contentIdentifier(),note_id:req.id}));
            return JSON.stringify({protocol_version:1,attachments:page,done:req.offset+page.length>=all.length,
                next_offset:req.offset+page.length,content_downloaded:false,metadata_complete:false});
        }
        const body = n.body();
        const plain = n.plaintext();
        return JSON.stringify({protocol_version:1,item:{id:n.id(),source_id:folder.source,folder_id:req.folder_id,
            title:n.name().slice(0,1000),html:body.slice(0,100000),text:plain.slice(0,100000),
            truncated:body.length>100000 || plain.length>100000,modified:n.modificationDate() ? n.modificationDate().toISOString() : null,
            locked:false,shared:n.shared(),content_complete:false,omitted_features:['native_objects','attachments','collaboration_metadata']}});
    }
    if (!Number.isInteger(req.limit) || req.limit < 1 || req.limit > 100 || !Number.isInteger(req.offset) || req.offset < 0 || req.offset > 100000) throw Error('invalid_page');
    const sorted = ids.slice().sort();
    const page = sorted.slice(req.offset,req.offset+req.limit).map(id => {
        const n=folder.object.notes.byId(id);
        return {id:id,title:n.name().slice(0,1000),source_id:folder.source,folder_id:req.folder_id};
    });
    return JSON.stringify({protocol_version:1,items:page,done:req.offset+page.length>=sorted.length,
        next_offset:req.offset+page.length,pagination_consistency:'live_offset_may_shift'});
}
