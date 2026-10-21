"""Explicit completion of a known verified partial COPY; never creates a copy.

Not registered as an MCP tool. Operator supplies exact previously reconciled IDs.
No broad EXPUNGE, deletion of destination, write retry or only-copy deletion.
"""
import hashlib
import email
from email import policy
from . import core


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
