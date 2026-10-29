"""Exact proposal admission and the local-owner receipt interface.

Strict receipts are the default; trusted-client routing is explicit configuration.
The receipt interface is never registered as an MCP tool.

    icloud-agents-approve PLAN_ID

Reads the immutable proposal, displays exact batch/content, and requires an explicit
confirmation through /dev/tty. Protect runtime directory from other users. This is an
operator workflow boundary, NOT isolation from a process with the same OS identity.
"""

import fcntl
import argparse
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .core import SafetyError, canonical, digest


PLAN_KINDS = frozenset({"trash", "send", "calendar_change", "contact_change", "notes_change", "reminders_change", "notes_access", "reminders_access", "notes_recovery", "notes_grant", "reminders_grant", "notes_move_recovery", "notes_edit_recovery", "reminders_observation_recovery"})


def runtime_root():
    configured = os.environ.get("ICLOUD_RUNTIME_ROOT")
    if configured is not None:
        path = Path(configured)
        if not path.is_absolute() or any(ord(c) < 32 for c in configured):
            raise SafetyError("Runtime root must be an explicit absolute private path")
        return path
    return Path.home() / "Library" / "Application Support" / "icloud-for-agents" / "runtime"


def authorization_path(plan):
    """Informational routing only; execution independently enforces every guard."""
    if plan['kind'] == 'trash' and os.environ.get('ICLOUD_CLEANUP_GRANT_ID'):
        return 'standing-cleanup', False
    if any(item.get('operation') == 'delete' for item in plan['payload'].get('changes', [])):
        return 'local-receipt', True
    if plan['kind'] in {'notes_change','reminders_change'} and isinstance(plan['payload'].get('standing_grant'),dict):
        return 'standing-native', False
    policy = os.environ.get('ICLOUD_APPROVAL_POLICY', 'local-receipt')
    if policy not in {'local-receipt','trusted-client'}:
        raise SafetyError('Unknown approval policy')
    return policy, policy == 'local-receipt'


class PlanStore:
    def __init__(self, root=None, clock=time.time):
        self.root = Path(root) if root is not None else runtime_root()
        self.clock = clock

    def ready(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise SafetyError("Runtime directory must be owned by current user, real directory, mode 0700")

    def path(self, plan_id, suffix):
        if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
            raise SafetyError("Invalid plan identifier")
        return self.root / (plan_id + suffix)

    def write_once(self, path, value):
        self.ready()
        encoded = canonical(value)
        if len(encoded) > 250000:
            raise SafetyError("Plan exceeds review limit; prepare a smaller batch")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def read(self, path):
        self.ready()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise SafetyError("Plan/receipt must be an owned regular file with mode 0600")
            raw = stream.read(250001)
        if len(raw) > 250000:
            raise SafetyError("Oversized plan file")
        return json.loads(raw)

    def prepare(self, kind, payload):
        if kind not in PLAN_KINDS:
            raise SafetyError("Unsupported approval proposal kind")
        plan_id = uuid.uuid4().hex
        plan = {"plan_id": plan_id, "kind": kind, "created": self.clock(),
                "expires": self.clock() + 1800, "payload": payload}
        self.write_once(self.path(plan_id, ".json"), plan)
        path, receipt_required = authorization_path(plan)
        return {"plan_id": plan_id, "review": plan, "sha256": digest(plan),
                "approval_required": True, "authorization_path": path,
                "local_receipt_required": receipt_required,
                "operator_command": "icloud-agents-approve " + plan_id if receipt_required else None}

    def proposal(self, plan_id):
        plan = self.read(self.path(plan_id, ".json"))
        if plan.get("plan_id") != plan_id or plan.get("kind") not in PLAN_KINDS:
            raise SafetyError("Invalid proposal")
        if self.clock() > plan["expires"]:
            raise SafetyError("Plan expired; prepare a new proposal")
        if self.path(plan_id, ".cancelled").exists():
            raise SafetyError("Plan cancelled; prepare a new proposal if needed")
        if self.path(plan_id, ".started").exists():
            raise SafetyError("Plan already started: inspect its result; never replay automatically")
        return plan

    def prepare_no_effect_retry(self, original_id, evidence):
        """Local reviewed maintenance only; never an MCP tool or auto recovery.

        One retry of ONE absent calendar create, with identical payload. Original
        result/started files remain immutable. Caller must perform live read-only
        reconciliation; executor independently checks GET404 before its PUT.
        """
        original = self.read(self.path(original_id, ".json"))
        result = self.read(self.path(original_id, ".result"))
        changes = original.get("payload", {}).get("changes", [])
        if (original.get("kind") != "calendar_change" or original.get("reconciliation_of")
                or result.get("status") != "failed_or_uncertain" or result.get("completed") != []
                or len(changes) != 1 or changes[0].get("operation") != "create"):
            raise SafetyError("Only one unresolved calendar create can use no-effect reconciliation")
        expected = {"checked_at", "url", "http_status", "uid", "bounded_uid_matches", "scope", "material_fix", "request_sha256"}
        if not isinstance(evidence, dict) or set(evidence) != expected:
            raise SafetyError("Exact no-effect evidence required")
        if (type(evidence["checked_at"]) not in (int,float) or not 0 <= self.clock()-evidence["checked_at"] <= 60
                or evidence["url"] != changes[0]["url"] or evidence["http_status"] != 404
                or evidence["bounded_uid_matches"] != 0 or not evidence["scope"]
                or evidence["material_fix"] != "rfc6638-schedule-reply-delete-only"
                or evidence["request_sha256"] != digest(original["payload"])
                or ("UID:"+evidence["uid"]+"\r\n") not in changes[0]["content"]):
            raise SafetyError("No-effect evidence is stale, ambiguous or identifies a different request")
        if os.environ.get("ICLOUD_APPROVAL_POLICY") != "trusted-client" or os.environ.get("ICLOUD_MAIL_ACCOUNT") != original["payload"].get("account"):
            raise SafetyError("Reconciliation requires owner-enabled trusted-client account")
        retry_id = uuid.uuid4().hex
        retry = dict(original, plan_id=retry_id, created=self.clock(), expires=self.clock()+1800,
                     reconciliation_of=original_id)
        record = {"status":"reconciled_no_effect_for_one_retry", "original_id":original_id,
                  "original_sha256":digest(original), "original_result_sha256":digest(result),
                  "retry_id":retry_id, "retry_sha256":digest(retry), "evidence":evidence,
                  "reviewed_decision":"One same-identity conditional retry after explicit reviewed reconciliation; not a human receipt"}
        # Reservation first: a crash can strand recovery but cannot create two retries.
        self.write_once(self.path(original_id, ".reconciled"), record)
        self.write_once(self.path(retry_id, ".json"), retry)
        return {"plan_id":retry_id,"review":retry,"sha256":digest(retry)}

    def _reconciled_for(self, original_id, retry):
        record = self.read(self.path(original_id, ".reconciled"))
        original = self.read(self.path(original_id, ".json"))
        result = self.read(self.path(original_id, ".result"))
        return (record.get("status") == "reconciled_no_effect_for_one_retry"
                and record.get("original_sha256") == digest(original)
                and record.get("original_result_sha256") == digest(result)
                and record.get("retry_id") == retry["plan_id"]
                and record.get("retry_sha256") == digest(retry)
                and retry.get("reconciliation_of") == original_id
                and retry["payload"] == original["payload"]
                and 0 <= self.clock()-record["evidence"]["checked_at"] <= 60)

    def complete_reconciliation(self, retry_id, verification):
        """Local reviewed read-back proof; never erase or rewrite original audit."""
        retry = self.read(self.path(retry_id, ".json"))
        original_id = retry.get("reconciliation_of")
        if not original_id:
            raise SafetyError("Not a reconciled retry")
        record = self.read(self.path(original_id, ".reconciled"))
        result = self.read(self.path(retry_id, ".result"))
        item = retry["payload"]["changes"][0]
        if (record.get("retry_id") != retry_id or record.get("retry_sha256") != digest(retry)
                or result.get("status") != "applied" or len(result.get("completed", [])) != 1
                or result["completed"][0].get("url") != item["url"]
                or set(verification) != {"url", "uid", "resource_sha256", "all_fields_verified", "checked_at"}
                or verification["url"] != item["url"] or verification["uid"] != record["evidence"]["uid"]
                or verification["all_fields_verified"] is not True
                or not re.fullmatch(r"[0-9a-f]{64}", verification["resource_sha256"])
                or not 0 <= self.clock()-verification["checked_at"] <= 60):
            raise SafetyError("Exact successful read-back proof required")
        self.write_once(self.path(original_id, ".reconciled-result"),
                        {"status":"reconciled_retry_verified", "retry_id":retry_id,
                         "retry_result_sha256":digest(result), "record_sha256":digest(record), "verification":verification})

    def _native_reminder_recovery_for(self,original_id,recovery):
        if recovery.get('kind')!='reminders_observation_recovery' or recovery.get('payload',{}).get('original_id')!=original_id:return False
        from .native_reminder_exchange import inspect
        from .native_reminder_observation import OBSERVABLE_CHANGES
        original=self.read(self.path(original_id,'.json'));result=self.read(self.path(original_id,'.result'))
        items=original.get('payload',{}).get('changes',[]);payload=recovery.get('payload',{});observed=payload.get('observed',{})
        if (original.get('kind')!='reminders_change' or not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict) or not isinstance(observed,dict) or items[0].get('operation') not in OBSERVABLE_CHANGES
                or self.read(self.path(original_id,'.started')).get('sha256')!=digest(original)
                or result.get('status')!='failed_or_uncertain' or result.get('phase')!='mutation'
                or result.get('attempted')!=[] or result.get('completed')!=[] or list(self.root.glob(original_id+'.attempt-*'))
                or payload.get('account')!=original['payload'].get('account') or payload.get('original_sha256')!=digest(original)
                or payload.get('original_result_sha256')!=digest(result) or payload.get('original_item_sha256')!=digest(items[0])
                or payload.get('context',{}).get('source_ids')!=original['payload'].get('context',{}).get('source_ids')
                or payload.get('context',{}).get('backend')!=original['payload'].get('context',{}).get('backend')
                or payload.get('operation')!='accept_observed_unchanged_reminder'):return False
        terminal=inspect(self,original)
        legacy='legacy_acknowledgement' in payload
        unknown=payload.get('accept_unknown_dispatch') is True
        if 'accept_unknown_dispatch' in payload and not unknown:return False
        if legacy and unknown:return False
        if legacy:
            from .native_reminder_recovery import LEGACY_ACK, missing_legacy_exchange
            if (payload['legacy_acknowledgement']!=LEGACY_ACK or not missing_legacy_exchange(self,original_id)
                    or terminal!={'terminal_response':'not_recorded','helper_reported_mutation_dispatched':None}
                    or payload.get('request_record_sha256')!=digest(None) or payload.get('response_record_sha256')!=digest(None)
                    or payload.get('effects',{}).get('missing_terminal_evidence')!='No original request or terminal response was retained. No dispatch, stop or absence of effects is proven by this recovery.'
                    or payload.get('effects',{}).get('helper_stop_evidence')!='Owner attestation only; independently verify the original helper has stopped before accepting.'):return False
        else:
            if terminal!={'terminal_response':'recorded','helper_reported_mutation_dispatched':None if unknown else True}:return False
            if unknown:
                from .native_reminder_recovery import UNKNOWN_DISPATCH_EFFECT
                if payload.get('effects',{}).get('retained_terminal_unknown_dispatch')!=UNKNOWN_DISPATCH_EFFECT:return False
            request=self.read(self.path(original_id,'.native-reminder-request-0'));response=self.read(self.path(original_id,'.native-reminder-response-0'))
            if (response.get('response_kind')!='error' or payload.get('request_record_sha256')!=digest(request)
                    or payload.get('response_record_sha256')!=digest(response)):return False
        item=items[0]
        coverage='only_fields_exposed_by_the_current_adapter'
        if item['operation']=='move_reminder' and 'source_graph' in item.get('before',{}):coverage='all_reviewed_subtree_snapshots_and_both_bounded_list_graphs'
        if item['operation']=='create_subtask':
            if legacy or observed.get('preassigned_child_observation')!={'id':item.get('child_id'),'found_in_selected_live_scope':False,'found_in_recovery_view':False,'coverage':'selected_EventKit_scope_and_provider_recovery_view','absence_globally_proven':False}:return False
            coverage='parent_snapshot_and_preassigned_child_scope_observations'
        if (observed.get('plan_id')!=original_id or observed.get('reminder_id')!=item.get('id') or observed.get('source_id')!=item.get('source_id')
                or observed.get('observed_snapshot_matches_before') is not True or observed.get('terminal_response')!=terminal['terminal_response']
                or observed.get('helper_reported_mutation_dispatched') is not terminal['helper_reported_mutation_dispatched'] or observed.get('data_mutated') is not False
                or observed.get('original_uncertainty_retained') is not True or observed.get('causal_dispatch_proven') is not False
                or observed.get('snapshot_coverage')!=coverage
                or observed.get('observation_consistency')!='separate_live_reads_may_change'
                or observed.get('do_not_retry') is not True):return False
        identity=recovery['plan_id'];abandoned=self.path(identity,'.native-reminder-recovery-abandoned')
        if abandoned.exists():
            if self.read(abandoned)!={'recovery_sha256':digest(recovery),'native_data_changed':False}:raise SafetyError('Reminder observation withdrawal evidence is invalid')
            return False
        proof=self.path(original_id,'.native-reminder-recovery-result')
        if proof.exists() and self.read(proof).get('recovery_id')!=identity:return False
        for started in self.root.glob('*.started'):
            if started.stem==identity:continue
            previous=self.read(self.path(started.stem,'.json'))
            if previous.get('kind')!='reminders_observation_recovery' or previous.get('payload',{}).get('original_id')!=original_id:continue
            outcome=self.path(started.stem,'.result')
            if outcome.exists() and self.read(outcome).get('recovery_state')=='observed_unchanged_reminder_accepted_after_review' and not self.path(started.stem,'.native-reminder-recovery-abandoned').exists():return False
        return True

    def _native_reminder_recovery_completed(self,original_id):
        path=self.path(original_id,'.native-reminder-recovery-result')
        if not path.exists():return False
        proof=self.read(path);recovery=self.read(self.path(proof['recovery_id'],'.json'));result=self.read(self.path(proof['recovery_id'],'.result'));started=self.read(self.path(proof['recovery_id'],'.started'))
        policy=recovery['payload']['context']['approval_policy'];authority='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt'
        if started.get('authorization')!=authority or started.get('account')!=recovery['payload']['account']:return False
        if policy=='local-receipt':
            receipt=self.read(self.path(recovery['plan_id'],'.approved'))
            if receipt.get('plan_id')!=recovery['plan_id'] or receipt.get('sha256')!=digest(recovery):return False
        if 'legacy_acknowledgement' in recovery['payload'] and result.get('owner_acknowledgement')!=recovery['payload']['legacy_acknowledgement']:return False
        return (self._native_reminder_recovery_for(original_id,recovery) and proof.get('status')=='reviewed_unchanged_reminder_accepted'
                and started.get('sha256')==digest(recovery) and result.get('status')=='completed'
                and result.get('recovery_state')=='observed_unchanged_reminder_accepted_after_review' and result.get('native_data_changed') is False
                and result.get('causal_dispatch_proven') is False and result.get('original_result_unchanged') is True
                and result.get('do_not_retry') is True and proof.get('recovery_result_sha256')==digest(result)
                and proof.get('original_result_sha256')==digest(self.read(self.path(original_id,'.result')))
                and result.get('after_sha256')==digest(recovery['payload']['observed']) and proof.get('observed_sha256')==result['after_sha256'])

    def _native_edit_recovery_for(self,original_id,recovery,*,completed=False):
        if recovery.get('kind')!='notes_edit_recovery' or recovery.get('payload',{}).get('original_id')!=original_id:return False
        from .native_protocol import mutation_request
        original=self.read(self.path(original_id,'.json'));result=self.read(self.path(original_id,'.result'));items=original.get('payload',{}).get('changes',[]);payload=recovery.get('payload',{});observed=payload.get('observed',{})
        request_matches=False
        if len(items)==1 and isinstance(items[0],dict):
            request=mutation_request('notes',items[0]);request_matches=payload.get('request_sha256')==digest(request)
            if not request_matches and completed and 'folder_guard' in request.get('change',{}):
                # Completed receipts predating folder_guard bind the original
                # envelope and immutable transport record. Never admit a new
                # recovery or dispatch with the historical envelope.
                request['change'].pop('folder_guard')
                path=self.path(original_id,'.native-exchange-0')
                if path.exists():
                    binding=self.read(path);backend=original.get('payload',{}).get('context',{}).get('backend')
                    fingerprint=binding.get('helper_fingerprint')
                    request_matches=(isinstance(fingerprint,str) and len(fingerprint)==64
                        and isinstance(backend,str) and backend.startswith('notes-creation-v1:'+fingerprint)
                        and binding.get('backend_id')==backend and binding.get('plan_sha256')==digest(original)
                        and binding.get('request_sha256')==payload.get('request_sha256')==digest(request))
        if (original.get('kind')!='notes_change' or len(items)!=1 or items[0].get('operation') not in {'append_note','prepend_note','replace_text','update_note'}
                or result.get('status')!='failed_or_uncertain' or result.get('phase') not in {'mutation','verification'}
                or payload.get('account')!=original['payload'].get('account') or payload.get('original_sha256')!=digest(original)
                or payload.get('original_result_sha256')!=digest(result) or payload.get('original_item_sha256')!=digest(items[0])
                or not request_matches or payload.get('operation')!='accept_observed_unchanged_note'
                or payload.get('context',{}).get('source_ids')!=original['payload'].get('context',{}).get('source_ids')):return False
        item=items[0]
        if (observed.get('plan_id')!=original_id or observed.get('note_id')!=item.get('id') or observed.get('source_id')!=item.get('source_id')
                or observed.get('original_folder_id')!=item.get('folder_id') or not isinstance(observed.get('observed_folder_id'),str) or not observed['observed_folder_id']
                or observed.get('observable_content_matches_before') is not True or observed.get('terminal_exchange_observed') is not True
                or not isinstance(observed.get('terminal_exchange_sha256'),str) or len(observed['terminal_exchange_sha256'])!=64
                or any(c not in '0123456789abcdef' for c in observed['terminal_exchange_sha256'])
                or observed.get('helper_outcome') not in {'error_response','success_response'} or observed.get('do_not_retry') is not True
                or observed.get('data_mutated') is not False or observed.get('original_uncertainty_retained') is not True or observed.get('causal_dispatch_proven') is not False):return False
        identity=recovery['plan_id'];abandoned=self.path(identity,'.native-edit-recovery-abandoned')
        if abandoned.exists():
            if self.read(abandoned)!={'recovery_sha256':digest(recovery),'native_data_changed':False}:raise SafetyError('Edit observation withdrawal evidence is invalid')
            return False
        proof=self.path(original_id,'.native-edit-recovery-result')
        if proof.exists() and self.read(proof).get('recovery_id')!=identity:return False
        for started in self.root.glob('*.started'):
            if started.stem==identity:continue
            previous=self.read(self.path(started.stem,'.json'))
            if previous.get('kind')!='notes_edit_recovery' or previous.get('payload',{}).get('original_id')!=original_id:continue
            outcome=self.path(started.stem,'.result')
            if outcome.exists() and self.read(outcome).get('recovery_state')=='observed_unchanged_note_accepted_after_review' and not self.path(started.stem,'.native-edit-recovery-abandoned').exists():return False
        return True

    def _native_edit_recovery_completed(self,original_id):
        path=self.path(original_id,'.native-edit-recovery-result')
        if not path.exists():return False
        proof=self.read(path);recovery=self.read(self.path(proof['recovery_id'],'.json'));result=self.read(self.path(proof['recovery_id'],'.result'));started=self.read(self.path(proof['recovery_id'],'.started'))
        policy=recovery['payload']['context']['approval_policy'];authority='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt'
        if started.get('authorization')!=authority or started.get('account')!=recovery['payload']['account']:return False
        if policy=='local-receipt':
            receipt=self.read(self.path(recovery['plan_id'],'.approved'))
            if receipt.get('plan_id')!=recovery['plan_id'] or receipt.get('sha256')!=digest(recovery):return False
        return (self._native_edit_recovery_for(original_id,recovery,completed=True) and proof.get('status')=='reviewed_unchanged_note_accepted'
                and started.get('sha256')==digest(recovery) and result.get('status')=='completed'
                and result.get('recovery_state')=='observed_unchanged_note_accepted_after_review' and result.get('native_data_changed') is False
                and result.get('causal_dispatch_proven') is False and proof.get('recovery_result_sha256')==digest(result)
                and proof.get('original_result_sha256')==digest(self.read(self.path(original_id,'.result')))
                and result.get('after_sha256')==digest(recovery['payload']['observed']) and proof.get('observed_sha256')==result['after_sha256'])

    def _native_move_recovery_abandoned(self,recovery):
        path=self.path(recovery['plan_id'],'.native-move-recovery-abandoned')
        if not path.exists():return False
        if self.read(path)!={'recovery_sha256':digest(recovery),'native_data_changed':False}:raise SafetyError('Move observation withdrawal evidence is invalid')
        return True

    def _native_move_recovery_for(self,original_id,recovery):
        if recovery.get('kind')!='notes_move_recovery' or recovery.get('payload',{}).get('original_id')!=original_id:return False
        from .native_move_recovery import CONTENT_KEYS
        from .native_protocol import mutation_request
        original=self.read(self.path(original_id,'.json'));result=self.read(self.path(original_id,'.result'));items=original.get('payload',{}).get('changes',[]);payload=recovery.get('payload',{});observed=payload.get('observed',{})
        if (original.get('kind')!='notes_change' or len(items)!=1 or items[0].get('operation') not in {'move_note','restore_note','delete_note'}
                or result.get('status')!='failed_or_uncertain' or result.get('phase') not in {'mutation','verification'}
                or recovery.get('kind')!='notes_move_recovery' or payload.get('original_id')!=original_id
                or payload.get('account')!=original['payload'].get('account') or payload.get('original_sha256')!=digest(original)
                or payload.get('original_result_sha256')!=digest(result) or payload.get('original_item_sha256')!=digest(items[0])
                or payload.get('operation')!='accept_observed_known_move_state'
                or payload.get('context',{}).get('source_ids')!=original['payload'].get('context',{}).get('source_ids')):return False
        item=items[0];destination=item['fields']['destination_folder_id'];location=observed.get('observed_folder_id')
        if (observed.get('note_id')!=item.get('id') or observed.get('source_id')!=item['source_id']
                or observed.get('source_folder_id')!=item['folder_id'] or observed.get('destination_folder_id')!=destination
                or location not in {item['folder_id'],destination} or observed.get('location')!=('source' if location==item['folder_id'] else 'destination')
                or observed.get('content_sha256')!=digest({k:item['before']['note'].get(k) for k in CONTENT_KEYS})
                or observed.get('attachments_sha256')!=digest(item['before']['attachments'])
                or payload.get('terminal_exchange',{}).get('request_sha256')!=digest(mutation_request('notes',item))):return False
        if item['operation'] in {'restore_note','delete_note'}:
            from .native_notes_restore import stable_roles
            if (observed.get('persisted_location')!={'id':item['id'],'folder_id':location,'source_id':item['source_id'],'content_read':False,'database_writes':False}
                    or not isinstance(observed.get('recovery_roles'),list) or not isinstance(item.get('before',{}).get('roles'),list)
                    or stable_roles(observed['recovery_roles'])!=stable_roles(item['before']['roles'])):return False
        identity=recovery['plan_id']
        if self._native_move_recovery_abandoned(recovery):return False
        proof=self.path(original_id,'.native-move-recovery-result')
        if proof.exists() and self.read(proof).get('recovery_id')!=identity:return False
        for started in self.root.glob('*.started'):
            if started.stem==identity:continue
            previous=self.read(self.path(started.stem,'.json'))
            if previous.get('kind')!='notes_move_recovery' or previous.get('payload',{}).get('original_id')!=original_id:continue
            outcome=self.path(started.stem,'.result')
            if (outcome.exists() and self.read(outcome).get('recovery_state')=='observed_known_move_state_accepted_after_review'
                    and not self._native_move_recovery_abandoned(previous)):return False
        return True

    def _native_move_recovery_completed(self,original_id):
        path=self.path(original_id,'.native-move-recovery-result')
        if not path.exists():return False
        proof=self.read(path);recovery=self.read(self.path(proof['recovery_id'],'.json'));result=self.read(self.path(proof['recovery_id'],'.result'));started=self.read(self.path(proof['recovery_id'],'.started'))
        policy=recovery['payload']['context']['approval_policy']
        authority='local-receipt' if policy=='local-receipt' else 'trusted-client-policy; not an independent human receipt'
        if started.get('authorization')!=authority or started.get('account')!=recovery['payload']['account']:return False
        if policy=='local-receipt':
            receipt=self.read(self.path(recovery['plan_id'],'.approved'))
            if receipt.get('plan_id')!=recovery['plan_id'] or receipt.get('sha256')!=digest(recovery):return False
        return (self._native_move_recovery_for(original_id,recovery) and proof.get('status')=='reviewed_known_move_state_accepted'
                and started.get('sha256')==digest(recovery) and result.get('status')=='completed'
                and result.get('recovery_state')=='observed_known_move_state_accepted_after_review' and result.get('native_data_changed') is False
                and proof.get('recovery_result_sha256')==digest(result) and proof.get('original_result_sha256')==digest(self.read(self.path(original_id,'.result')))
                and result.get('after_sha256')==digest(recovery['payload']['observed']) and proof.get('observed_sha256')==result['after_sha256'])

    def _native_recovery_for(self,original_id,recovery):
        path=self.path(original_id,'.native-recovery')
        if not path.exists():return False
        record=self.read(path);original=self.read(self.path(original_id,'.json'));result=self.read(self.path(original_id,'.result'))
        changes=original.get('payload',{}).get('changes',[]);payload=recovery.get('payload',{})
        return (record.get('status')=='reserved_reviewed_notes_association'
                and record.get('original_sha256')==digest(original) and record.get('original_result_sha256')==digest(result)
                and record.get('recovery_id')==recovery.get('plan_id') and record.get('recovery_sha256')==digest(recovery)
                and recovery.get('native_recovery_of')==original_id and recovery.get('kind')=='notes_recovery'
                and original.get('kind')=='notes_change' and len(changes)==1 and changes[0].get('operation')=='create_note'
                and result.get('status')=='failed_or_uncertain' and result.get('phase')=='mutation'
                and result.get('attempted')==[] and result.get('completed')==[]
                and payload.get('account')==original['payload'].get('account')
                and payload.get('original_sha256')==digest(original) and payload.get('original_result_sha256')==digest(result)
                and payload.get('source_id')==changes[0].get('source_id') and payload.get('folder_id')==changes[0].get('folder_id')
                and payload.get('requested_fields')==changes[0].get('fields')
                and payload.get('operation') in {'accept_observed_creation','repair_title_only_candidate'}
                and isinstance(payload.get('candidate_id'),str) and bool(payload.get('candidate_id')))

    def _native_recovery_completed(self,original_id):
        path=self.path(original_id,'.native-recovery-result')
        if not path.exists():return False
        proof=self.read(path);recovery=self.read(self.path(proof['recovery_id'],'.json'));result=self.read(self.path(proof['recovery_id'],'.result'))
        return (self._native_recovery_for(original_id,recovery) and proof.get('status')=='reviewed_observed_notes_state_verified'
                and proof.get('recovery_result_sha256')==digest(result) and result.get('status')=='completed'
                and result.get('recovery_state')=='observed_state_accepted_after_review'
                and proof.get('after_sha256')==result.get('after_sha256')
                and proof.get('original_result_sha256')==digest(self.read(self.path(original_id,'.result'))))

    def _native_correction_for(self, original_id, correction):
        """Exact reserved correction of one known-ID reminder update, never a create retry."""
        path=self.path(original_id,'.native-correction')
        if not path.exists():return False
        record=self.read(path);original=self.read(self.path(original_id,'.json'));result=self.read(self.path(original_id,'.result'))
        changes=original.get('payload',{}).get('changes',[]);repairs=correction.get('payload',{}).get('changes',[])
        attempts=result.get('attempted',[])
        return (record.get('status')=='reserved_known_update_correction'
                and record.get('original_sha256')==digest(original) and record.get('original_result_sha256')==digest(result)
                and record.get('correction_id')==correction.get('plan_id') and record.get('correction_sha256')==digest(correction)
                and correction.get('native_correction_of')==original_id
                and original.get('kind')==correction.get('kind') and original.get('kind') in {'reminders_change','notes_change'}
                and original['payload'].get('account')==correction['payload'].get('account')
                and not original.get('native_correction_of')
                and result.get('status')=='failed_or_uncertain' and result.get('phase')=='verification'
                and len(changes)==len(repairs)==len(attempts)==1
                and changes[0].get('operation')==repairs[0].get('operation')
                and changes[0].get('operation') in ({'rename_folder'} if original.get('kind')=='notes_change' else {'update_reminder','set_recurrence','set_list_color','set_flagged','set_urgent'})
                and changes[0].get('id')==repairs[0].get('id')==attempts[0].get('result',{}).get('id')
                and changes[0].get('source_id')==repairs[0].get('source_id'))

    def _native_correction_completed(self, original_id):
        path=self.path(original_id,'.native-correction-result')
        if not path.exists():return False
        proof=self.read(path);correction=self.read(self.path(proof['correction_id'],'.json'))
        result=self.read(self.path(proof['correction_id'],'.result'))
        return (self._native_correction_for(original_id,correction)
                and proof.get('status')==('original_before_state_restored_after_correction' if correction['payload'].get('correction_target')=='before' else 'original_requested_fields_verified_after_correction')
                and proof.get('correction_result_sha256')==digest(result) and result.get('status')=='applied'
                and proof.get('original_result_sha256')==digest(self.read(self.path(original_id,'.result'))))

    def _native_reconciliation_completed(self, plan_id):
        path=self.path(plan_id,'.native-reconciled')
        if not path.exists():return False
        record=self.read(path);plan=self.read(self.path(plan_id,'.json'));result=self.read(self.path(plan_id,'.result'))
        if plan.get('kind') not in {'notes_change','reminders_change'}:return False
        attempts=result.get('attempted',[])
        return (record.get('status')=='native_readback_verified' and record.get('plan_sha256')==digest(plan)
                and record.get('result_sha256')==digest(result) and record.get('attempts_sha256')==digest(attempts)
                and len(attempts)==len(plan['payload'].get('changes',[])) and bool(attempts)
                and record.get('attempt_record_hashes')==[digest(self.read(self.path(plan_id,'.attempt-'+str(i)))) for i in range(len(attempts))])

    def _reconciliation_completed(self, original_id):
        if self._native_reconciliation_completed(original_id) or self._native_correction_completed(original_id) or self._native_recovery_completed(original_id):return True
        path = self.path(original_id, ".reconciled-result")
        if not path.exists():
            return False
        done = self.read(path)
        record = self.read(self.path(original_id, ".reconciled"))
        retry = self.read(self.path(record["retry_id"], ".json"))
        result = self.read(self.path(record["retry_id"], ".result"))
        return (done.get("status") == "reconciled_retry_verified" and done.get("record_sha256") == digest(record)
                and done.get("retry_id") == record["retry_id"] and record.get("retry_sha256") == digest(retry)
                and retry.get("reconciliation_of") == original_id
                and record.get("original_sha256") == digest(self.read(self.path(original_id, ".json")))
                and record.get("original_result_sha256") == digest(self.read(self.path(original_id, ".result")))
                and done.get("retry_result_sha256") == digest(result) and result.get("status") == "applied")

    def native_predispatch_proof(self,plan_id,result):
        plan=self.read(self.path(plan_id,'.json'));items=plan.get('payload',{}).get('changes',[])
        attempts=result.get('attempted');completed=result.get('completed')
        if (plan.get('kind') not in {'notes_change','reminders_change'}
                or result.get('phase') not in {'pre_dispatch','snapshot_recheck'}
                or not isinstance(attempts,list) or attempts!=completed or not 0<=len(attempts)<len(items)
                or self.read(self.path(plan_id,'.started')).get('sha256')!=digest(plan)):
            raise SafetyError('Only a proven native stop before dispatch can settle without provider reconciliation')
        paths=set(self.root.glob(plan_id+'.attempt-*'))
        expected={self.path(plan_id,'.attempt-'+str(i)) for i in range(len(attempts))}
        if paths!=expected:raise SafetyError('Native attempt records are incomplete or contain later dispatch evidence')
        hashes=[]
        for i,attempt in enumerate(attempts):
            record=self.read(self.path(plan_id,'.attempt-'+str(i)))
            if (attempt.get('index')!=i or attempt.get('operation')!=items[i]['operation']
                    or record!={'plan_sha256':digest(plan),'attempt':attempt}):raise SafetyError('Native completed attempt proof does not match')
            hashes.append(digest(record))
        return {'plan_sha256':digest(plan),'result_phase':result['phase'],'not_dispatched_index':len(attempts),'attempt_record_hashes':hashes}

    def _native_stopped_completed(self,plan_id):
        path=self.path(plan_id,'.native-stopped')
        if not path.exists():return False
        record=self.read(path);result=self.read(self.path(plan_id,'.result'))
        return (result.get('status')=='failed_or_uncertain' and record=={'original_result_sha256':digest(result),
                'proof':self.native_predispatch_proof(plan_id,result),'status':'stopped_before_dispatch_finalized'})

    def operation_settled(self,plan_id):
        path=self.path(plan_id,'.result')
        if not path.exists():
            plan=self.read(self.path(plan_id,'.json'))
            revoked=self.path(plan_id,'.native-grant-revoked')
            if plan.get('kind') in {'notes_grant','reminders_grant'} and revoked.exists():
                return self.read(revoked).get('grant_sha256')==digest(plan)
            return False
        result=self.read(path)
        if self._native_stopped_completed(plan_id) or self._native_move_recovery_completed(plan_id) or self._native_edit_recovery_completed(plan_id) or self._native_reminder_recovery_completed(plan_id):return True
        if result.get('status')=='stopped_before_dispatch':
            return result.get('pre_dispatch_evidence')==self.native_predispatch_proof(plan_id,result)
        if result.get('status') in {'applied','moved','sent','completed'}:return True
        if result.get('status')!='refused_without_dispatch':return False
        plan=self.read(self.path(plan_id,'.json'));index=result.get('refused_index');changes=plan.get('payload',{}).get('changes',[])
        evidence=result.get('refusal_evidence',{});attempts=result.get('attempted',[])
        if (plan.get('kind') not in {'notes_change','reminders_change'} or type(index) is not int or not 0<=index<len(changes)
                or result.get('plan_sha256')!=digest(plan) or evidence.get('mutation_dispatched') is not False
                or result.get('refused_item_mutation_dispatched') is not False
                or not isinstance(evidence.get('helper_response_sha256'),str)
                or not re.fullmatch(r'[0-9a-f]{64}',evidence['helper_response_sha256'])
                or len(attempts)!=index or result.get('completed')!=attempts):return False
        from .native_protocol import mutation_request
        expected=mutation_request(plan['kind'].removesuffix('_change'),changes[index])
        if evidence.get('request_sha256')!=digest(expected):return False
        for position,attempt in enumerate(attempts):
            if (attempt.get('index')!=position or attempt.get('operation')!=changes[position].get('operation')
                    or self.read(self.path(plan_id,'.attempt-'+str(position)))!={'plan_sha256':digest(plan),'attempt':attempt}):return False
        return True

    def consume(self, plan_id, account):
        policy = os.environ.get("ICLOUD_APPROVAL_POLICY", "local-receipt")
        if policy not in {"local-receipt", "trusted-client"}:
            raise SafetyError("Invalid approval policy")
        self.ready()
        fd = os.open(self.root / "approval-policy.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise SafetyError("Invalid approval policy lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.environ.get("ICLOUD_ENABLE_POLICY_MANAGEMENT") == "1":
                from .services import enabled
                if enabled("mail"):
                    from .managed_policy import assert_settled_context
                    assert_settled_context(account)
            plan = self.proposal(plan_id)
            if plan["payload"].get("account") != account:
                raise SafetyError("Approval belongs to a different account")
            irreversible = any(item.get("operation") == "delete" for item in plan["payload"].get("changes", []))
            trusted = policy == "trusted-client" and not irreversible
            if trusted:
                if os.environ.get("ICLOUD_MAIL_ACCOUNT") != account:
                    raise SafetyError("Trusted-client policy account mismatch")
                # Serialize proposal admission. Unfinished or uncertain writes block
                # further trusted-client writes, even through a different plan.
                for started in self.root.glob("*.started"):
                    previous = self.read(self.path(started.stem, ".json"))
                    if previous["payload"].get("account") != account:
                        continue
                    if not self.operation_settled(started.stem):
                        if not (self._reconciliation_completed(started.stem) or (plan.get('kind')=='notes_move_recovery' and self._native_move_recovery_for(started.stem,plan)) or (plan.get('kind')=='notes_edit_recovery' and self._native_edit_recovery_for(started.stem,plan)) or (plan.get('kind')=='reminders_observation_recovery' and self._native_reminder_recovery_for(started.stem,plan)) or (plan.get("native_recovery_of")==started.stem and self._native_recovery_for(started.stem,plan)) or (plan.get("native_correction_of")==started.stem and self._native_correction_for(started.stem,plan)) or (plan.get("reconciliation_of") == started.stem and self._reconciled_for(started.stem, plan))):
                            raise SafetyError("Prior write unfinished or uncertain; reconcile before a new write")
                authority = "trusted-client-policy; not an independent human receipt"
            else:
                receipt = self.read(self.path(plan_id, ".approved"))
                if receipt.get("sha256") != digest(plan) or receipt.get("plan_id") != plan_id:
                    raise SafetyError("Approval does not match exact proposal")
                authority = "local-receipt"
            self.write_once(self.path(plan_id, ".started"),
                            {"started": self.clock(), "sha256": digest(plan), "authorization": authority, "account": account})
            return plan
        finally:
            os.close(fd)

    def finish(self, plan_id, result):
        self.write_once(self.path(plan_id, ".result"), result)
        return result

    def status(self, plan_id):
        reminder_abandoned=self.path(plan_id,'.native-reminder-recovery-abandoned')
        if reminder_abandoned.exists():
            plan=self.read(self.path(plan_id,'.json'));record=self.read(reminder_abandoned)
            if plan.get('kind')!='reminders_observation_recovery' or record!={'recovery_sha256':digest(plan),'native_data_changed':False}:raise SafetyError('Reminder observation withdrawal does not bind its plan')
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False,'do_not_retry':True}
        if self._native_reminder_recovery_completed(plan_id):
            return {'status':'observed_unchanged_reminder_accepted','original_status':'failed_or_uncertain','causal_dispatch_proven':False,'do_not_retry':True}
        edit_abandoned=self.path(plan_id,'.native-edit-recovery-abandoned')
        if edit_abandoned.exists():
            plan=self.read(self.path(plan_id,'.json'));record=self.read(edit_abandoned)
            if plan.get('kind')!='notes_edit_recovery' or record!={'recovery_sha256':digest(plan),'native_data_changed':False}:raise SafetyError('Edit observation withdrawal does not bind its plan')
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False,'do_not_retry':True}
        if self._native_edit_recovery_completed(plan_id):
            return {'status':'observed_unchanged_note_accepted','original_status':'failed_or_uncertain','causal_dispatch_proven':False,'do_not_retry':True}
        abandoned=self.path(plan_id,'.native-move-recovery-abandoned')
        if abandoned.exists():
            plan=self.read(self.path(plan_id,'.json'));record=self.read(abandoned)
            if plan.get('kind')!='notes_move_recovery' or record!={'recovery_sha256':digest(plan),'native_data_changed':False}:raise SafetyError('Move observation withdrawal does not bind its plan')
            return {'status':'abandoned','original_uncertainty_retained':True,'native_data_changed':False,'do_not_retry':True}
        if self._native_move_recovery_completed(plan_id):
            record=self.read(self.path(plan_id,'.native-move-recovery-result'));result=self.read(self.path(record['recovery_id'],'.result'))
            return {'status':'observed_known_move_state_accepted','original_status':'failed_or_uncertain','location':result['location'],'causal_dispatch_proven':False,'do_not_retry':True}
        if self._native_stopped_completed(plan_id):return {'status':'stopped_before_dispatch_finalized','original_status':'failed_or_uncertain','do_not_retry':True}
        if self.path(plan_id,'.cancelled').exists():
            record=self.read(self.path(plan_id,'.cancelled'));plan=self.read(self.path(plan_id,'.json'))
            if record.get('plan_sha256')!=digest(plan):raise SafetyError('Cancellation does not bind this exact plan')
            return {'status':'cancelled','plan_sha256':digest(plan),'do_not_retry':True}
        if self._native_recovery_completed(plan_id):
            return {"status":"observed_state_accepted_after_review","original_status":"failed_or_uncertain","identity_provenance":"reviewed association under selected policy, not a returned creation ID or independent human proof","do_not_retry":True}
        if self._native_correction_completed(plan_id):
            proof=self.read(self.path(plan_id,'.native-correction-result'))
            if proof.get('status')=='original_before_state_restored_after_correction':return {'status':'restored_before_after_explicit_correction','original_status':'failed_or_uncertain','original_request_succeeded':False,'do_not_retry':True}
            return {"status":"verified_after_explicit_correction","original_status":"failed_or_uncertain","do_not_retry":True}
        if self._native_reconciliation_completed(plan_id):
            return {"status":"applied_after_readback_reconciliation","original_status":"failed_or_uncertain","do_not_retry":True}
        result = self.path(plan_id, ".result")
        if result.exists():
            value=self.read(result)
            if value.get("status") in {"refused_without_dispatch","stopped_before_dispatch"} and not self.operation_settled(plan_id):raise SafetyError("Native refusal evidence does not match the exact plan")
            return value
        if self.path(plan_id, ".started").exists():
            return {"status": "started_or_uncertain", "do_not_retry": True}
        plan = self.proposal(plan_id)
        path, receipt_required = authorization_path(plan)
        if self.path(plan_id, ".approved").exists():
            receipt = self.read(self.path(plan_id, ".approved"))
            if receipt.get("plan_id") != plan_id or receipt.get("sha256") != digest(plan):
                raise SafetyError("Receipt does not match current proposal")
            return {"status":"approved", "authorization_path":path}
        status = "awaiting_local_approval" if receipt_required else ("awaiting_standing_scope_validation" if path in {"standing-cleanup","standing-native"} else "awaiting_client_authorized_execution")
        return {"status":status, "authorization_path":path, "local_receipt_required":receipt_required}


def approve_interactively(store, plan_id):
    plan = store.proposal(plan_id)
    # Escape all controls in mail-derived text before rendering it in a terminal.
    review = json.dumps(plan, ensure_ascii=True, indent=2)
    fingerprint = digest(plan)
    labels = {"trash": "TRASH", "send": "SEND", "calendar_change": "APPROVE CALENDAR", "contact_change": "APPROVE CONTACTS", "notes_change": "APPROVE NOTES", "reminders_change": "APPROVE REMINDERS", "notes_access":"ALLOW NOTES OS REQUEST", "reminders_access":"ALLOW REMINDERS OS REQUEST", "notes_recovery":"APPROVE NOTES RECOVERY", "notes_move_recovery":"ACCEPT OBSERVED NOTES MOVE", "notes_edit_recovery":"ACCEPT OBSERVED NOTES EDIT", "reminders_observation_recovery":"ACCEPT OBSERVED REMINDER", "notes_grant":"APPROVE NOTES GRANT", "reminders_grant":"APPROVE REMINDERS GRANT"}
    label = labels[plan["kind"]]
    if any(item.get("operation") == "delete" for item in plan["payload"].get("changes", [])):
        label = "IRREVERSIBLE DELETE " + label
    phrase = label + " " + plan_id
    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write(review + "\nSHA256: " + fingerprint + "\n")
        terminal.write("Review every UID/subject or recipient/content above.\n")
        terminal.write("Approve this exact proposal by typing: " + phrase + "\n> ")
        terminal.flush()
        answer = terminal.readline().rstrip("\n")
    if answer != phrase:
        raise SafetyError("No approval recorded")
    if digest(store.proposal(plan_id)) != fingerprint:
        raise SafetyError("Proposal changed while it was being reviewed")
    store.write_once(store.path(plan_id, ".approved"),
                     {"plan_id": plan_id, "sha256": fingerprint, "approved_at": store.clock()})


def main():
    parser = argparse.ArgumentParser(description="Human-only exact Trash batch/send review; no mail connection")
    parser.add_argument("plan_id")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Checkout containing optional private runtime-directory settings")
    args = parser.parse_args()
    if (args.project_root / "local/settings.json").exists():
        from .local_config import read_settings
        settings = read_settings(args.project_root)
        if settings.get("runtime_directory"):
            os.environ["ICLOUD_RUNTIME_ROOT"] = settings["runtime_directory"]
    approve_interactively(PlanStore(), args.plan_id)
