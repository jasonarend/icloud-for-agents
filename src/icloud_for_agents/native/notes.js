// Fixed read-only JXA protocol. User data is JSON, never executable script source.
ObjC.import('Foundation');
function run() {
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
    if (text.length > 65536) throw Error('invalid_request');
    const req = JSON.parse(text);
    if (req.protocol_version !== 1 || !['sources','folders','query','get'].includes(req.action)) throw Error('unsupported_action');
    const app = Application('Notes');
    const accounts = app.accounts();
    if (req.action === 'sources') return JSON.stringify({protocol_version:1, sources: accounts.map(a => ({id:a.id(), title:a.name()}))});
    if (!Array.isArray(req.source_ids) || req.source_ids.length === 0) throw Error('source_scope_required');
    const selected = accounts.filter(a => req.source_ids.includes(a.id()));
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
    if (req.action === 'folders') return JSON.stringify({protocol_version:1, folders:folders.map(f => ({id:f.object.id(), title:f.object.name(), source_id:f.source}))});
    if (!req.folder_id) throw Error('folder_scope_required');
    const matches = folders.filter(f => f.object.id() === req.folder_id);
    if (matches.length !== 1) throw Error('not_found_in_scope');
    const folder = matches[0];
    const ids = folder.object.notes.id();
    if (req.action === 'get') {
        if (!ids.includes(req.id)) throw Error('not_found_in_scope');
        const n = folder.object.notes.byId(req.id);
        // Locked notes must not be read or implicitly unlocked.
        let locked;
        try { locked = n.passwordProtected(); } catch (_) { throw Error('lock_status_unavailable'); }
        if (locked) return JSON.stringify({protocol_version:1,item:{id:req.id,locked:true,content_available:false}});
        const body = n.body();
        const plain = n.plaintext();
        return JSON.stringify({protocol_version:1,item:{id:n.id(),source_id:folder.source,folder_id:req.folder_id,
            title:n.name(),html:body.slice(0,100000),text:plain.slice(0,100000),
            truncated:body.length>100000 || plain.length>100000,modified:n.modificationDate().toISOString(),
            locked:false,content_complete:false,omitted_features:['native_objects','attachments','collaboration_metadata']}});
    }
    if (!Number.isInteger(req.limit) || req.limit < 1 || req.limit > 100 || !Number.isInteger(req.offset) || req.offset < 0 || req.offset > 100000) throw Error('invalid_page');
    const sorted = ids.slice().sort();
    const page = sorted.slice(req.offset,req.offset+req.limit).map(id => {
        const n=folder.object.notes.byId(id);
        return {id:id,title:n.name(),source_id:folder.source,folder_id:req.folder_id};
    });
    return JSON.stringify({protocol_version:1,items:page,done:req.offset+page.length>=sorted.length,
        next_offset:req.offset+page.length,pagination_consistency:'live_offset_may_shift'});
}
