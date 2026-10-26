// Fixed mutation protocol, invoked only by the approved-plan executor.
// This module never accepts scripts, arbitrary commands, paths or approval flags.
ObjC.import('Foundation');
function applyNotes(app, req) {
    let stage='validation', mutationDispatched=false;
    try {
    if (req.protocol_version!==1 || req.action!=='apply' || !Array.isArray(req.source_ids)) throw Error('invalid_request');
    const item=req.change;
    if (!item || !['create_note','create_folder','repair_note_body','append_note','update_note'].includes(item.operation) || !req.source_ids.includes(item.source_id)) throw Error('unsupported_operation');
    const accounts=app.accounts().filter(a=>a.id()===item.source_id);
    if(accounts.length!==1)throw Error('not_found_in_scope');
    const account=accounts[0];
    const fields=item.fields;
    if(!fields || typeof fields.title!=='string' || fields.title.length<1 || fields.title.length>1000)throw Error('invalid_fields');
    const folders=[],seen=new Set();
    function walk(parent,depth) {
        if(depth>32 || folders.length>10000)throw Error('folder_limit');
        parent.folders().forEach(f=>{
            if(seen.has(f.id()))return;
            seen.add(f.id());folders.push(f);walk(f,depth+1);
        });
    }
    walk(account,0);
    if(item.operation==='create_folder') {
        if(folders.some(f=>f.container().id()===account.id() && f.name()===fields.title))throw Error('duplicate_folder');
        mutationDispatched=true;
        const folder=app.Folder({name:fields.title});
        account.folders.push(folder);
        return JSON.stringify({protocol_version:1,result:{id:folder.id(),source_id:item.source_id,saved_locally:true}});
    }
    stage='folder_lookup';
    const matches=folders.filter(f=>f.id()===item.folder_id);
    if(matches.length!==1)throw Error('not_found_in_scope');
    const folder=matches[0];
    if(folder.shared())throw Error('shared_write_not_supported');
    if(typeof fields.html!=='string' || fields.html.length>30000)throw Error('invalid_fields');
    // Notes derives its title from the first body paragraph. Supplying both name
    // and body can leave a title-only draft, depending on property assignment order.
    const escapeText=text=>text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    if(['append_note','update_note'].includes(item.operation)) {
        const before=item.before && item.before.note;
        if(!before || !folder.notes.id().includes(item.id))throw Error('not_found_in_scope');
        const note=folder.notes.byId(item.id), modified=note.modificationDate();
        if(note.passwordProtected() || note.shared() || note.attachments().length!==0 || !modified)throw Error('unsupported_edit_target');
        if(note.name()!==before.title || note.body()!==before.html || modified.toISOString()!==before.modified)throw Error('snapshot_changed');
        // The Python snapshot validator accepts only known basic HTML. Refuse
        // native objects here too, before setting an existing note body.
        if(/<(object|img|audio|video|iframe|embed|table)\b/i.test(before.html))throw Error('unsupported_edit_target');
        stage='note_insert';
        mutationDispatched=true;
        note.body=fields.html;
        return JSON.stringify({protocol_version:1,result:{id:note.id(),source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
    }
    if(item.operation==='repair_note_body') {
        const before=item.before && item.before.note;
        if(!before || !folder.notes.id().includes(item.id))throw Error('not_found_in_scope');
        const note=folder.notes.byId(item.id);
        if(note.passwordProtected() || note.shared() || note.attachments().length!==0)throw Error('unsupported_repair_target');
        const canonical='<div>'+note.name().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>';
        if(note.body().trim()!==canonical)throw Error('unsupported_repair_target');
        const modified=note.modificationDate();
        if(note.name()!==before.title || note.body()!==before.html ||
           (modified?modified.toISOString():null)!==before.modified)throw Error('snapshot_changed');
        stage='note_insert';
        mutationDispatched=true;
        note.body='<div>'+escapeText(fields.title)+'</div>'+fields.html;
        return JSON.stringify({protocol_version:1,result:{id:note.id(),source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
    }
    stage='membership_before';
    const before=new Set(folder.notes.id());
    stage='note_constructor';
    mutationDispatched=true;
    const draft=app.Note({body:'<div>'+escapeText(fields.title)+'</div>'+fields.html});
    stage='note_insert';
    folder.notes.push(draft);
    // Resolve through the destination collection, not the unbound constructor
    // object. Never issue another creation if the identity is uncertain.
    stage='membership_after';
    const after=folder.notes.id();
    let createdID=null;
    try {const bound=draft.id();if(after.includes(bound) && !before.has(bound))createdID=bound;} catch(_) {}
    if(!createdID) {
        // Saving may also replace an older temporary Core Data URI with a
        // permanent URI. Such unrelated transitions must not select that note.
        const matching=after.filter(id=>!before.has(id) && folder.notes.byId(id).name()===fields.title);
        if(matching.length!==1)throw Error('created_identity_uncertain');
        createdID=matching[0];
    }
    return JSON.stringify({protocol_version:1,result:{id:createdID,source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
    } catch(error) {
        const match=String(error).match(/-\d{3,6}/);
        const known=['invalid_request','unsupported_operation','not_found_in_scope','invalid_fields','folder_limit','duplicate_folder','shared_write_not_supported','created_identity_uncertain','unsupported_repair_target','snapshot_changed','unsupported_edit_target'];
        const category=known.find(value=>String(error).includes(value)) || 'provider_exception';
        return JSON.stringify({protocol_version:1,error:'notes_operation_failed',stage:stage,category:category,mutation_dispatched:mutationDispatched,
            error_code:match?Number(match[0]):-2700});
    }
}
