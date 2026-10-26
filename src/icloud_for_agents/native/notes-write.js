// Fixed mutation protocol, invoked only by the approved-plan executor.
// This module never accepts scripts, arbitrary commands, paths or approval flags.
ObjC.import('Foundation');
function applyNotes(app, req) {
    if (req.protocol_version!==1 || req.action!=='apply' || !Array.isArray(req.source_ids)) throw Error('invalid_request');
    const item=req.change;
    if (!item || !['create_note','create_folder'].includes(item.operation) || !req.source_ids.includes(item.source_id)) throw Error('unsupported_operation');
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
        const folder=app.Folder({name:fields.title});
        account.folders.push(folder);
        return JSON.stringify({protocol_version:1,result:{id:folder.id(),source_id:item.source_id,saved_locally:true}});
    }
    const matches=folders.filter(f=>f.id()===item.folder_id);
    if(matches.length!==1)throw Error('not_found_in_scope');
    const folder=matches[0];
    if(folder.shared())throw Error('shared_write_not_supported');
    if(typeof fields.html!=='string' || fields.html.length>30000)throw Error('invalid_fields');
    // Notes derives its title from the first body paragraph. Supplying both name
    // and body can leave a title-only draft, depending on property assignment order.
    const escapeText=text=>text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    const before=new Set(folder.notes.id());
    folder.notes.push(app.Note({body:'<div>'+escapeText(fields.title)+'</div>'+fields.html}));
    // Resolve through the destination collection, not the unbound constructor
    // object. Never issue another creation if the identity is uncertain.
    const created=folder.notes.id().filter(id=>!before.has(id));
    if(created.length!==1)throw Error('created_identity_uncertain');
    return JSON.stringify({protocol_version:1,result:{id:created[0],source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
}
