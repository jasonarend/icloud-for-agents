"""Explicit completion of a known verified partial COPY; never creates a copy.

Not registered as an MCP tool. Operator supplies exact previously reconciled IDs.
No broad EXPUNGE, deletion of destination, write retry or only-copy deletion.
"""
import hashlib
import email
from email import policy
from . import core


def _mail_copy_context(client, record, mapping, overrides):
    """Bind ordinary recovery to an exact mapped COPY and its configured roles."""
    operation, intent = record.get("operation"), record.get("intent", {})
    if operation not in {"archive", "restore"} or not isinstance(mapping, dict) or mapping.get("method") != "UID COPY":
        raise core.SafetyError("Recovery requires a recorded exact COPY mapping; unknown or MOVE outcomes are not replayed")
    source, destination = intent["folder"], record["destination"]
    if source == destination or not client.use_uid or not client.has_capability("UIDPLUS"):
        raise core.SafetyError("Exact copy completion requires distinct folders and UIDPLUS")
    core.exact_folder(client, destination)
    trash = core.role_folder(client, "Trash", overrides)
    if (operation == "archive" and (source == trash or destination != core.role_folder(client, "Archive", overrides))
            or operation == "restore" and (source != trash or destination == trash)):
        raise core.SafetyError("Mail recovery folder roles changed")
    pairs = mapping.get("uid_mapping")
    if (not isinstance(pairs, dict) or set(pairs) != {str(n) for n in core.batch(intent["uids"])}
            or len(set(pairs.values())) != len(pairs)):
        raise core.SafetyError("Recovery mapping does not cover the exact original batch")
    core.uid(mapping["destination_uidvalidity"])
    for value in pairs.values():
        core.uid(value)
    return source, destination, intent["uidvalidity"], mapping["destination_uidvalidity"], pairs


def _mail_metadata(client, folder, validity, message_uid):
    core.select(client, folder, validity, readonly=True)
    rows = client.fetch([message_uid], ["FLAGS", "RFC822.SIZE", core.HEADER_FETCH])
    if message_uid not in rows:
        return None
    row = rows[message_uid]
    return {"header_sha256": hashlib.sha256(core.body_bytes(row)).hexdigest(),
            "size": row.get(b"RFC822.SIZE"), "flags": sorted(set(core.flags(row)) - {"\\Recent"})}


def _mail_full(client, folder, validity, message_uid, expected, allow_deleted=False):
    core.select(client, folder, validity, readonly=True)
    rows = client.fetch([message_uid], ["FLAGS", "RFC822.SIZE", "BODY.PEEK[]<0.262145>"])
    if message_uid not in rows:
        raise core.SafetyError("Exact Mail recovery object disappeared")
    row = rows[message_uid]
    raw, size = core.body_bytes(row), row.get(b"RFC822.SIZE")
    flags = set(core.flags(row)) - {"\\Recent"}
    if (type(size) is not int or not 0 <= size <= 262144 or len(raw) != size
            or size != expected["size"] or ("\\Deleted" in flags and not allow_deleted)
            or flags - {"\\Deleted"} != set(expected["flags"])):
        raise core.SafetyError("Copy completion requires complete bounded matching content and retained flags")
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size": size, "flags": sorted(flags)}


def _mail_pair(client, source, source_validity, source_uid, destination, target_validity, target_uid, before):
    expected = {"header_sha256": before["header_sha256"], "size": before["size"],
                "flags": sorted(set(before["flags"]) - {"\\Recent", "\\Deleted"})}
    target_meta = _mail_metadata(client, destination, target_validity, target_uid)
    if target_meta != expected:
        raise core.SafetyError("Retained destination changed; no source completion is permitted")
    target = _mail_full(client, destination, target_validity, target_uid, expected)
    source_meta = _mail_metadata(client, source, source_validity, source_uid)
    if source_meta is None:
        return {"source_uid": source_uid, "destination_uid": target_uid,
                "source_present": False, "target": target, "causal_dispatch_proven": False}
    if dict(source_meta, flags=sorted(set(source_meta["flags"]) - {"\\Deleted"})) != expected:
        raise core.SafetyError("Source changed; no source completion is permitted")
    original = _mail_full(client, source, source_validity, source_uid, expected, allow_deleted=True)
    if original["sha256"] != target["sha256"]:
        raise core.SafetyError("Exact source and retained copy bodies differ")
    return {"source_uid": source_uid, "destination_uid": target_uid, "source_present": True,
            "source": original, "target": target, "causal_dispatch_proven": False}


def mail_copy_evidence(client, record, mapping, overrides):
    source, destination, source_validity, target_validity, pairs = _mail_copy_context(client, record, mapping, overrides)
    before = {item["uid"]: item for item in record["before"]}
    if set(before) != {int(key) for key in pairs}:
        raise core.SafetyError("Original Mail snapshot does not match its mapping")
    return {str(n): _mail_pair(client, source, source_validity, n, destination, target_validity, pairs[str(n)], before[n])
            for n in sorted(before)}


def complete_mail_copy(client, record, mapping, expected, overrides):
    """Complete only a separately reviewed known COPY; never create another copy."""
    source, destination, source_validity, target_validity, pairs = _mail_copy_context(client, record, mapping, overrides)
    if mail_copy_evidence(client, record, mapping, overrides) != expected:
        raise core.SafetyError("Mail copy evidence changed after review")
    before = {item["uid"]: item for item in record["before"]}
    for n in sorted(before):
        observed = _mail_pair(client, source, source_validity, n, destination, target_validity, pairs[str(n)], before[n])
        if observed != expected[str(n)]:
            raise core.SafetyError("Exact Mail copy pair changed before source removal")
        if not observed["source_present"]:
            continue
        core.select(client, source, source_validity, readonly=False)
        client.delete_messages([n], silent=True)
        client.uid_expunge([n])
        core.select(client, source, source_validity, readonly=True)
        if client.fetch([n], ["FLAGS"]):
            raise core.SafetyError("Source removal remains uncertain; never replay automatically")
        target_meta = _mail_metadata(client, destination, target_validity, pairs[str(n)])
        target = _mail_full(client, destination, target_validity, pairs[str(n)], target_meta)
        if target != observed["target"]:
            raise core.SafetyError("Retained destination changed during source completion")
    return {"status": "completed_existing_mail_copy", "copied_again": False}


def fingerprint(client, folder, validity, message_uid, message_id, readonly=True):
    core.select(client, folder, validity, readonly=readonly)
    rows = client.fetch([message_uid], ['FLAGS', 'RFC822.SIZE', 'BODY.PEEK[]<0.262145>'])
    if message_uid not in rows:
        raise core.SafetyError('Exact reconciliation UID missing; stop without retry')
    row = rows[message_uid]
    size = row.get(b'RFC822.SIZE')
    raw = core.body_bytes(row)
    if type(size) is not int or size > 262144 or len(raw) != size:
        raise core.SafetyError('Reconciliation requires both complete bounded message bodies')
    msg = email.message_from_bytes(raw, policy=policy.default)
    if str(msg.get('Message-ID', '')).strip() != message_id:
        raise core.SafetyError('Reconciliation Message-ID mismatch')
    flags = set(core.flags(row)) - {'\\Recent'}
    if '\\Deleted' in flags:
        raise core.SafetyError('Reconciliation refuses pre-deleted messages')
    return {'sha256':hashlib.sha256(raw).hexdigest(), 'size':size, 'flags':sorted(flags)}


def complete_archive_copy(client, source, source_validity, source_uid,
                          destination, destination_validity, destination_uid,
                          message_id, overrides):
    source_uid, destination_uid = core.uid(source_uid), core.uid(destination_uid)
    if source == destination or core.role_folder(client, 'Archive', overrides) != destination:
        raise core.SafetyError('Reconciliation must retain the exact configured Archive copy')
    if not client.use_uid or not client.has_capability('UIDPLUS'):
        raise core.SafetyError('Exact reconciliation requires UIDPLUS; no changes made')
    original = fingerprint(client, source, source_validity, source_uid, message_id)
    target = fingerprint(client, destination, destination_validity, destination_uid, message_id)
    if original != target:
        raise core.SafetyError('Reconciliation copies differ; no changes made')
    # Recheck destination, then source with write selection immediately before
    # targeted removal. A concurrent server change still yields uncertain status;
    # the retained Archive copy is never modified by this operation.
    if fingerprint(client, destination, destination_validity, destination_uid, message_id) != target:
        raise core.SafetyError('Retained Archive copy changed; no changes made')
    if fingerprint(client, source, source_validity, source_uid, message_id, readonly=False) != original:
        raise core.SafetyError('Source changed; no changes made')
    client.delete_messages([source_uid], silent=True)
    client.uid_expunge([source_uid])
    if client.fetch([source_uid], ['FLAGS']):
        raise core.SafetyError('Source removal unconfirmed; reconcile without retry')
    if fingerprint(client, destination, destination_validity, destination_uid, message_id) != target:
        raise core.SafetyError('Retained Archive verification changed; reconcile without retry')
    return {'status':'completed_existing_archive_copy','source_uid':source_uid,
            'destination_uid':destination_uid,'destination_uidvalidity':destination_validity,
            'retained_sha256':target['sha256'],'flags':target['flags']}
