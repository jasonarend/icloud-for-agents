"""Read-only exact-object inspection after one uncertain Notes edit.

This does not settle the plan or prove whether a dispatch occurred. A caller
must supply the current physical folder; the original folder may no longer exist.
"""
from .core import SafetyError, digest
from .native_app import completed_exchange_evidence
from .native_move_recovery import CONTENT_KEYS
from .native_protocol import mutation_request


def inspect_uncertain_note_edit(engine, plan_id, folder_id):
    if engine.name!='notes' or not isinstance(folder_id,str) or not 1<=len(folder_id)<=1000:
        raise SafetyError('Select one exact current Notes folder')
    context=engine.context(True)
    plan=engine.store.read(engine.store.path(plan_id,'.json'))
    result=engine.store.read(engine.store.path(plan_id,'.result'))
    items=plan.get('payload',{}).get('changes',[])
    if (plan.get('kind')!='notes_change' or plan['payload'].get('account')!=engine.account
            or plan['payload'].get('context',{}).get('source_ids')!=context['source_ids']
            or plan['payload'].get('context',{}).get('backend')!=context['backend']
            or result.get('status')!='failed_or_uncertain' or result.get('phase') not in {'mutation','verification'}
            or len(items)!=1 or items[0].get('operation') not in {'append_note','prepend_note','replace_text','update_note'}
            or engine.store.read(engine.store.path(plan_id,'.started')).get('sha256')!=digest(plan)):
        raise SafetyError('Inspection requires one owned uncertain edit with unchanged source and backend')
    item=items[0];before=item.get('before',{});note=before.get('note',{})
    if (not isinstance(note,dict) or note.get('id')!=item.get('id') or note.get('folder_id')!=item.get('folder_id')
            or note.get('source_id')!=item.get('source_id') or not isinstance(before.get('attachments'),list)):
        raise SafetyError('The original exact Notes snapshot is incomplete')
    exchange=completed_exchange_evidence(mutation_request('notes',item),plan_id=plan_id,store=engine.store)
    observed=engine.adapter.note_snapshot(dict(item,folder_id=folder_id),rewrite=False,verify_folder_role=False)
    current=observed['note']
    if current.get('id')!=item['id'] or current.get('source_id')!=item['source_id'] or current.get('folder_id')!=folder_id:
        raise SafetyError('The observed note does not match the selected identity and source')
    unchanged=all(current.get(key)==note.get(key) for key in CONTENT_KEYS) and observed['attachments']==before['attachments']
    if engine.context(True)!=context:raise SafetyError('Notes access changed during observation')
    return {'plan_id':plan_id,'note_id':item['id'],'source_id':item['source_id'],'original_folder_id':item['folder_id'],
            'observed_folder_id':folder_id,'observable_content_matches_before':unchanged,
            'original_modified':note.get('modified'),'observed_modified':current.get('modified'),
            'terminal_exchange_observed':True,'helper_outcome':exchange['helper_outcome'],
            'terminal_exchange_sha256':digest(exchange),
            'data_mutated':False,'original_uncertainty_retained':True,'causal_dispatch_proven':False,'do_not_retry':True}
