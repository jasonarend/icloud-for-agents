// Fixed mutation protocol, invoked only by the approved-plan executor.
// This module never accepts scripts, arbitrary commands, paths or approval flags.
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

function applyNotes(app, req, beginMutation, nativePhase) {
    let stage='validation', mutationDispatched=false;
    function markMutation(){if(typeof beginMutation!=='function' || !beginMutation())throw Error('native_dispatch_cancelled');mutationDispatched=true;}
    try {
    if (req.protocol_version!==1 || req.action!=='apply' || !Array.isArray(req.source_ids)) throw Error('invalid_request');
    const item=req.change;
    if (!item || !['create_note','create_folder','repair_note_body','append_note','prepend_note','replace_text','update_note','move_note','restore_note','delete_note','rename_folder'].includes(item.operation) || !req.source_ids.includes(item.source_id)) throw Error('unsupported_operation');
    const restoring=item.operation==='restore_note',deleting=item.operation==='delete_note',moving=restoring || deleting || item.operation==='move_note';
    if(moving && (item.folder_guard!==(deleting?'verified_trash_destination':restoring?'verified_recovery_roles':'verified_store_roles') || !item.before || !Array.isArray(item.before.roles) || item.before.roles.length<1 || item.before.roles.length>64))throw Error('unverified_folder_role_move');
    const accounts=app.accounts().filter(a=>a.id()===item.source_id);
    if(accounts.length!==1)throw Error('not_found_in_scope');
    const account=accounts[0];
    const fields=item.fields;
    if(deleting && (!fields || fields.recoverable!==true))throw Error('invalid_recoverable_delete');
    if(!fields || typeof fields.title!=='string' || fields.title.length<1 || fields.title.length>1000)throw Error('invalid_fields');
    const folders=[],seen=new Set();
    function collect(identity) {
        let current=account.folders.byId(identity);const ancestry=new Set();
        for(let depth=0;depth<32;depth++) {
            const id=current.id();if(ancestry.has(id))throw Error('folder_limit');ancestry.add(id);
            if(!seen.has(id)){seen.add(id);folders.push(current);}
            const parent=current.container().id();if(parent===item.source_id)return;
            current=account.folders.byId(parent);
        }
        throw Error('folder_limit');
    }
    if(item.folder_id)collect(item.folder_id);
    if(item.parent_folder_id)collect(item.parent_folder_id);
    if(moving)collect(fields.destination_folder_id);
    if(['verified_store_roles','verified_recovery_roles','verified_trash_destination'].includes(item.folder_guard)) {
        const roleError=deleting?'unverified_folder_role_delete':restoring?'unverified_folder_role_restore':moving?'unverified_folder_role_move':'unverified_folder_role';
        const roles=item.before && item.before.roles;
        if(!Array.isArray(roles) || roles.length<1 || roles.length>64)throw Error(roleError);
        const roleIDs=new Set(roles.map(r=>r.folder_id));
        if(roleIDs.size!==roles.length || roles.length!==folders.length || folders.some(f=>{
            const role=roles.find(r=>r.folder_id===f.id());
            if(!role || role.source_id!==item.source_id || role.storage_kind!==(((restoring && role.folder_id===item.folder_id) || (deleting && role.folder_id===fields.destination_folder_id))?'special_smart_or_deleted':'regular') || role.native_folder_type!==(((restoring && role.folder_id===item.folder_id) || (deleting && role.folder_id===fields.destination_folder_id))?1:0) || role.marked_for_deletion!==false || role.has_smart_query!==false || role.title!==f.name())return true;
            const parent=f.container().id();
            if(parent===item.source_id)return role.parent_pk!==null;
            if((restoring && role.folder_id===item.folder_id) || (deleting && role.folder_id===fields.destination_folder_id))return true;
            const match=/\/ICFolder\/p([1-9][0-9]*)$/.exec(parent),pk=match?Number(match[1]):null;
            return !Number.isSafeInteger(pk) || role.parent_pk!==pk;
        }))throw Error(roleError);
    }
    // The native model is verified in the final Python snapshot. The ordinary
    // scripting path can safely identify only the account's creation default.
    const guardedFolder=item.operation==='create_folder'?item.parent_folder_id:item.folder_id;
    if(guardedFolder && ['create_note','create_folder','repair_note_body','append_note','prepend_note','replace_text','update_note'].includes(item.operation)) {
        if(item.folder_guard==='default_folder') {
            if(account.defaultFolder().id()!==guardedFolder)throw Error('unverified_folder_role');
        } else if(!['verified_model','verified_store_roles'].includes(item.folder_guard))throw Error('unverified_folder_role');
    }
    function requirePrivateFolder(folder) {
        const visited=new Set();let current=folder;
        for(let depth=0;depth<32;depth++) {
            const id=current.id();if(visited.has(id))throw Error('folder_limit');visited.add(id);
            if(current.shared()!==false)throw Error('shared_write_not_supported');
            const parent=current.container().id();if(parent===item.source_id)return;
            const matching=folders.filter(f=>f.id()===parent);
            if(matching.length!==1)throw Error('folder_ancestry_unavailable');current=matching[0];
        }
        throw Error('folder_limit');
    }
    if(item.operation==='create_folder') {
        let parent=account;
        if(item.parent_folder_id) {
            const matching=folders.filter(f=>f.id()===item.parent_folder_id);
            if(matching.length!==1)throw Error('not_found_in_scope');parent=matching[0];requirePrivateFolder(parent);
        }
        if(parent.folders().some(f=>f.container().id()===parent.id() && f.name()===fields.title))throw Error('duplicate_folder');
        markMutation();
        const folder=app.Folder({name:fields.title});
        markMutation();parent.folders.push(folder);
        return JSON.stringify({protocol_version:1,result:{id:folder.id(),source_id:item.source_id,parent_id:parent.id(),saved_locally:true}});
    }
    const matches=folders.filter(f=>f.id()===item.folder_id);
    if(matches.length!==1)throw Error('not_found_in_scope');
    const folder=matches[0];
    requirePrivateFolder(folder);
    if(item.operation==='rename_folder') {
        if(item.id!==item.folder_id || !item.before || !item.before.inventory)throw Error('invalid_fields');
        const inventory=notesFolderInventory(account,folder);
        if(inventory.folders.some(f=>f.shared || f.is_default))throw Error('unsupported_edit_target');
        if(nativePhase==='refresh_model' && item.writer_backend==='notes-shared') {
            const root=inventory.folders.find(f=>f.id===item.id), prior=item.before.inventory.folders.find(f=>f.id===item.id);
            if(!root || !prior || ![prior.title,fields.title].includes(root.title))throw Error('snapshot_changed');
            root.title=prior.title;
        }
        if(!notesMetadataEqual(inventory,item.before.inventory))throw Error('snapshot_changed');
        if(inventory.siblings.some(f=>f.title===fields.title))throw Error('duplicate_folder');
        if(nativePhase==='validate_model' && item.writer_backend==='notes-shared')return JSON.stringify({protocol_version:1,validated:true});
        if(nativePhase==='refresh_model' && item.writer_backend==='notes-shared') {
            if(folder.name()!==fields.title){stage='folder_cache_refresh';markMutation();folder.name=fields.title;}
            return JSON.stringify({protocol_version:1,result:{id:item.id,source_id:item.source_id,saved_locally:true}});
        }
        if(item.writer_backend!=='scripting' || item.legacy_title_restore!==true || !item.before.role || item.before.role.title!==fields.title)throw Error('unsupported_edit_target');
        stage='folder_rename';markMutation();folder.name=fields.title;
        return JSON.stringify({protocol_version:1,result:{id:item.id,source_id:item.source_id,saved_locally:true}});
    }
    if(moving) {
        const before=item.before && item.before.note;
        const destinations=folders.filter(f=>f.id()===fields.destination_folder_id);
        if(!before || destinations.length!==1 || fields.destination_folder_id===item.folder_id || !folder.notes.id().includes(item.id))throw Error('not_found_in_scope');
        const destination=destinations[0], note=folder.notes.byId(item.id), modified=note.modificationDate();
        requirePrivateFolder(destination);
        if(note.container().id()!==item.folder_id)throw Error('not_found_in_scope');
        if(destination.shared()!==false || note.passwordProtected()!==false || note.shared()!==false || !modified)throw Error('unsupported_edit_target');
        if(note.name()!==before.title || note.body()!==before.html || modified.toISOString()!==before.modified)throw Error('snapshot_changed');
        const inventory=note.attachments().map(a=>{const name=a.name();return {id:a.id(),name:typeof name==='string'?name.slice(0,1000):null,content_identifier:a.contentIdentifier(),note_id:item.id};}).sort((a,b)=>a.id.localeCompare(b.id));
        const expectedInventory=(item.before.attachments || []).map(a=>({id:a.id,name:a.name,content_identifier:a.content_identifier,note_id:a.note_id})).sort((a,b)=>a.id.localeCompare(b.id));
        if(inventory.length>100 || JSON.stringify(inventory)!==JSON.stringify(expectedInventory))throw Error('snapshot_changed');
        if(typeof note.move!=='function')throw Error('unsupported_move_command');
        stage='note_move';markMutation();
        note.move({to:destination});
        let visible=destination.notes.id().includes(item.id);
        for(let attempt=0;!visible && attempt<10;attempt++) { $.NSThread.sleepForTimeInterval(0.1);visible=destination.notes.id().includes(item.id); }
        if(!visible)throw Error('moved_identity_uncertain');
        return JSON.stringify({protocol_version:1,result:{id:item.id,source_id:item.source_id,folder_id:fields.destination_folder_id,saved_locally:true}});
    }
    if(typeof fields.html!=='string' || fields.html.length>90000)throw Error('invalid_fields');
    // Notes derives its title from the first body paragraph. Supplying both name
    // and body can leave a title-only draft, depending on property assignment order.
    const escapeText=text=>text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    if(['append_note','prepend_note','replace_text','update_note'].includes(item.operation)) {
        const before=item.before && item.before.note;
        if(!before || !folder.notes.id().includes(item.id))throw Error('not_found_in_scope');
        const note=folder.notes.byId(item.id), modified=note.modificationDate();
        if(note.container().id()!==item.folder_id)throw Error('not_found_in_scope');
        if(note.passwordProtected()!==false || note.shared()!==false || note.attachments().length!==0 || !modified)throw Error('unsupported_edit_target');
        if(note.name()!==before.title || note.body()!==before.html || modified.toISOString()!==before.modified)throw Error('snapshot_changed');
        // The Python snapshot validator accepts only known basic HTML. Refuse
        // native objects here too, before setting an existing note body.
        if(/<(object|img|audio|video|iframe|embed|table)\b/i.test(before.html))throw Error('unsupported_edit_target');
        stage='note_insert';
        markMutation();
        note.body=fields.html;
        return JSON.stringify({protocol_version:1,result:{id:note.id(),source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
    }
    if(item.operation==='repair_note_body') {
        const before=item.before && item.before.note;
        if(!before || !folder.notes.id().includes(item.id))throw Error('not_found_in_scope');
        const note=folder.notes.byId(item.id);
        if(note.container().id()!==item.folder_id)throw Error('not_found_in_scope');
        if(note.passwordProtected()!==false || note.shared()!==false || note.attachments().length!==0)throw Error('unsupported_repair_target');
        const canonical='<div>'+note.name().replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>';
        if(note.body().trim()!==canonical)throw Error('unsupported_repair_target');
        const modified=note.modificationDate();
        if(note.name()!==before.title || note.body()!==before.html ||
           (modified?modified.toISOString():null)!==before.modified)throw Error('snapshot_changed');
        stage='note_insert';
        markMutation();
        note.body='<div>'+escapeText(fields.title)+'</div>'+fields.html;
        return JSON.stringify({protocol_version:1,result:{id:note.id(),source_id:item.source_id,folder_id:item.folder_id,saved_locally:true}});
    }
    stage='membership_before';
    const before=new Set(folder.notes.id());
    stage='note_constructor';
    markMutation();
    const draft=app.Note({body:'<div>'+escapeText(fields.title)+'</div>'+fields.html});
    stage='note_insert';
    markMutation();folder.notes.push(draft);
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
        const known=['invalid_request','unsupported_operation','not_found_in_scope','invalid_fields','folder_limit','duplicate_folder','shared_write_not_supported','created_identity_uncertain','unsupported_repair_target','snapshot_changed','unsupported_edit_target','unsupported_move_command','unverified_folder_role_move','unverified_folder_role','moved_identity_uncertain','folder_ancestry_unavailable','native_dispatch_cancelled'];
        const category=known.find(value=>String(error).includes(value)) || 'provider_exception';
        return JSON.stringify({protocol_version:1,error:'notes_operation_failed',stage:stage,category:category,mutation_dispatched:mutationDispatched,
            error_code:match?Number(match[0]):-2700});
    }
}
