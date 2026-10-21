"""Bounded UID operations, deliberately independent of upstream's executable code."""

import base64
import email
import hashlib
import json
import re
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, formatdate

MAX_UID = 2**32 - 1
MAX_BATCH = 100
HEADER_FETCH = "BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)]<0.32768>"
BODY_LIMIT = 262144


class SafetyError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def uid(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_UID:
        raise SafetyError("UID must be a positive 32-bit integer")
    return value


def batch(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_BATCH:
        raise SafetyError("Batch must contain 1..100 explicit UIDs")
    result = [uid(value) for value in values]
    if len(set(result)) != len(result):
        raise SafetyError("Duplicate UIDs are not permitted")
    return sorted(result)


def text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def flags(data):
    return sorted(text(value) for value in data.get(b"FLAGS", ()))


def body_bytes(data):
    for key, value in data.items():
        if text(key).upper().startswith("BODY[") and isinstance(value, bytes):
            return value
    raise SafetyError("Server omitted the requested BODY.PEEK data")


def folders(client):
    """LIST exact server names; no guessed English/localized folder names."""
    return [{"name": name, "flags": [text(f) for f in fs],
             "delimiter": None if delimiter is None else text(delimiter)}
            for fs, delimiter, name in client.list_folders()]


def exact_folder(client, name):
    if not isinstance(name, str) or not name or len(name) > 1024 or any(c in name for c in "\r\n\x00"):
        raise SafetyError("Invalid exact folder name")
    matches = [f for f in folders(client) if f["name"] == name and
               "\\noselect" not in [s.lower() for s in f["flags"]]]
    if len(matches) != 1:
        raise SafetyError("Folder must exactly match one selectable LIST result")
    return name


def role_folder(client, role, overrides):
    listed = folders(client)
    special = "\\" + role.lower()
    matches = [f["name"] for f in listed if special in [s.lower() for s in f["flags"]]
               and "\\noselect" not in [s.lower() for s in f["flags"]]]
    if role in overrides:
        chosen = exact_folder(client, overrides[role])
        # Reject conflicting server-provided roles, especially archive->Trash.
        row = next(f for f in listed if f["name"] == chosen)
        conflicting = {"\\trash", "\\archive", "\\sent", "\\drafts", "\\junk"} - {special}
        if conflicting.intersection(s.lower() for s in row["flags"]):
            raise SafetyError("Configured folder has a conflicting special-use role")
        return chosen
    if len(matches) != 1:
        raise SafetyError("No unique special-use folder; user must configure an exact LIST name")
    return matches[0]


def select(client, folder, expected=None, readonly=True):
    exact_folder(client, folder)
    info = client.select_folder(folder, readonly=readonly)
    validity = uid(int(info[b"UIDVALIDITY"]))
    if expected is not None and validity != uid(expected):
        raise SafetyError("UIDVALIDITY changed: stop and restart discovery; do not reuse UIDs")
    return validity, info


def search_filter(sender=None, subject=None, unread_only=False):
    result = []
    for key, value in (("FROM", sender), ("SUBJECT", subject)):
        if value is not None:
            if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(c in value for c in "\r\n\x00"):
                raise SafetyError("Invalid search text")
            result += [key, value]
    if unread_only:
        result += ["UNSEEN"]
    return result


def page(client, account, folder, cursor=None, page_size=50, sender=None, subject=None, unread_only=False,
         after_uid=0, expected_uidvalidity=None):
    """Advance through bounded numeric UID windows, oldest first, fixed high watermark.

    Each call searches <=2000 UID positions and fetches <=100 bounded headers.
    An empty page may still have a next_cursor: keep following it to done=True.
    Cursor is portable JSON data, not an authorization grant. It contains no secrets.
    """
    if type(page_size) is not int or not 1 <= page_size <= MAX_BATCH:
        raise SafetyError("page_size must be 1..100")
    if type(after_uid) is not int or not 0 <= after_uid <= MAX_UID:
        raise SafetyError("after_uid must be a nonnegative 32-bit UID")
    criteria = search_filter(sender, subject, unread_only)
    identity = digest({"account": account, "folder": folder, "filter": criteria})
    if after_uid and not cursor and expected_uidvalidity is None:
        raise SafetyError("Incremental scan requires the completed snapshot's UIDVALIDITY")
    validity, info = select(client, folder, expected_uidvalidity)
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError()
            state = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if state["v"] != 1 or state["identity"] != identity or state["uidvalidity"] != validity:
                raise ValueError()
            low, high = state["next_uid"], state["high_uid"]
            if type(low) is not int or type(high) is not int or not 1 <= low <= high + 1 <= MAX_UID + 1:
                raise ValueError()
        except Exception as exc:
            raise SafetyError("Invalid checkpoint, changed folder/account/filter, or changed UIDVALIDITY") from exc
    else:
        high = int(info[b"UIDNEXT"]) - 1
        if not 0 <= high <= MAX_UID:
            raise SafetyError("Invalid UIDNEXT")
        low = after_uid + 1
        if low > high + 1:
            raise SafetyError("after_uid exceeds current snapshot; check UIDVALIDITY before incrementing")
    end = min(high, low + 1999)
    hits = [] if low > high else sorted(set(client.search(["UID", f"{low}:{end}"] + criteria, charset="UTF-8")))
    if any(type(n) is not int or not low <= n <= end for n in hits):
        raise SafetyError("Server returned UIDs outside requested window")
    chosen = hits[:page_size]
    next_uid = chosen[-1] + 1 if len(hits) > page_size else end + 1
    response = client.fetch(chosen, ["FLAGS", "RFC822.SIZE", HEADER_FETCH]) if chosen else {}
    messages = []
    for n in chosen:
        item = {"folder": folder, "uidvalidity": validity, "uid": n}
        if n not in response:
            item["status"] = "vanished"
        else:
            data = response[n]
            try:
                raw = body_bytes(data)
                msg = email.message_from_bytes(raw[:32768], policy=policy.default)
                item.update({k: str(msg.get(k, "")) for k in ("From", "To", "Cc", "Subject", "Date", "Message-ID")})
                item.update(flags=flags(data), size=data.get(b"RFC822.SIZE"), header_truncated=len(raw) >= 32768)
            except Exception:
                item["status"] = "header_unavailable"
        messages.append(item)
    state = {"v": 1, "identity": identity, "uidvalidity": validity, "high_uid": high, "next_uid": next_uid}
    done = next_uid > high
    return {"messages": messages, "uidvalidity": validity, "snapshot_high_uid": high, "done": done,
            "completed_snapshot": {"folder": folder, "uidvalidity": validity, "high_uid": high} if done else None,
            "scanned_uid_window": [low, end] if low <= high else [],
            "next_cursor": None if done else base64.urlsafe_b64encode(canonical(state).encode()).decode()}


def inspect(client, folder, uidvalidity, message_uid):
    message_uid = uid(message_uid)
    select(client, folder, uidvalidity, readonly=True)
    data = client.fetch([message_uid], ["FLAGS", "RFC822.SIZE", f"BODY.PEEK[]<0.{BODY_LIMIT}>"])
    if message_uid not in data:
        raise SafetyError("Message vanished")
    row = data[message_uid]
    raw = body_bytes(row)
    msg = email.message_from_bytes(raw[:BODY_LIMIT], policy=policy.default)
    from .body_text import mime_text
    extracted = mime_text(msg)
    return {"folder": folder, "uidvalidity": uidvalidity, "uid": message_uid, "flags": flags(row),
            "headers": {k: str(msg.get(k, "")) for k in ("From", "To", "Cc", "Subject", "Date", "Message-ID")},
            **extracted,
            "truncated": (row.get(b"RFC822.SIZE", len(raw)) > min(len(raw), BODY_LIMIT)
                          or extracted["body_extraction_limited"]),
            "content_is_untrusted": True}



def snapshot(client, folder, uidvalidity, uids):
    uids = batch(uids)
    select(client, folder, uidvalidity, readonly=True)
    rows = client.fetch(uids, ["FLAGS", "RFC822.SIZE", HEADER_FETCH])
    if set(rows) != set(uids):
        raise SafetyError("One or more batch UIDs vanished; rediscover and prepare a new batch")
    result = []
    for n in uids:
        if "\\Deleted" in flags(rows[n]):
            raise SafetyError("Batch includes an already Deleted message; reconcile manually")
        raw = body_bytes(rows[n])
        msg = email.message_from_bytes(raw[:32768], policy=policy.default)
        result.append({"uid": n, "flags": flags(rows[n]), "size": rows[n].get(b"RFC822.SIZE"),
                       "header_sha256": hashlib.sha256(raw).hexdigest(),
                       "from": str(msg.get("From", "")), "subject": str(msg.get("Subject", ""))})
    return result


def set_seen(client, folder, uidvalidity, uids, seen):
    uids = batch(uids)
    if type(seen) is not bool:
        raise SafetyError("seen must be boolean")
    snapshot(client, folder, uidvalidity, uids)
    select(client, folder, uidvalidity, readonly=False)
    # Add/remove one flag only: never replace the retained flags or stars.
    method = client.add_flags if seen else client.remove_flags
    method(uids, ["\\Seen"], silent=True)
    observed = client.get_flags(uids)
    if set(observed) != set(uids):
        raise SafetyError("Read-state verification missing messages; outcome uncertain, reconcile without retry")
    flags_after = {str(n): [text(f) for f in fs] for n, fs in observed.items()}
    if any(("\\Seen" in fs) != seen for fs in flags_after.values()):
        raise SafetyError("Read-state verification disagrees with requested state; reconcile without retry")
    return {"uids": uids, "seen": seen, "flags_after": flags_after}


def expand_uid_set(value):
    result = []
    for group in value.split(","):
        ends = group.split(":")
        a, b = int(ends[0]), int(ends[-1])
        uid(a); uid(b)
        if b < a or b - a >= MAX_BATCH or len(result) + b - a + 1 > MAX_BATCH:
            raise SafetyError("Unbounded COPYUID response")
        result.extend(range(a, b + 1))
    return result


def copy_mapping(response, uids):
    value = text(response)
    match = re.search(r"COPYUID (\d+) ([\d:,]+) ([\d:,]+)", value, re.I)
    if not match:
        raise SafetyError("COPYUID missing; destination identity uncertain, do not retry")
    source, destination = expand_uid_set(match[2]), expand_uid_set(match[3])
    if source != uids or len(destination) != len(source) or len(set(destination)) != len(destination):
        raise SafetyError("Unexpected COPYUID mapping; reconcile before any retry")
    return uid(int(match[1])), dict(zip(source, destination))


def safe_move(client, folder, uidvalidity, uids, destination, expected_snapshot):
    """Never general EXPUNGE/CLOSE. Require UIDPLUS for verifiable destination IDs.

    Prefer UID MOVE; fallback copies, verifies destination identities/flags, then UID
    STORE + UID EXPUNGE of only the copied source UIDs. No automatic mutation retries.
    """
    uids = batch(uids)
    if folder == destination:
        raise SafetyError("Source and destination must differ; Trash-to-Trash never purges")
    exact_folder(client, destination)
    current = snapshot(client, folder, uidvalidity, uids)
    if current != expected_snapshot:
        raise SafetyError("Batch changed since review; prepare a new plan")
    if not client.use_uid or not client.has_capability("UIDPLUS"):
        raise SafetyError("Safe verified UID move requires UID mode and UIDPLUS; no changes made")
    select(client, folder, uidvalidity, readonly=False)
    use_move = client.has_capability("MOVE")
    response = client.move(uids, destination) if use_move else client.copy(uids, destination)
    target_validity, mapping = copy_mapping(response, uids)
    select(client, destination, target_validity, readonly=True)
    copied = client.fetch(list(mapping.values()), ["FLAGS", "RFC822.SIZE", HEADER_FETCH])
    for item in current:
        target = copied.get(mapping[item["uid"]])
        if target is None or hashlib.sha256(body_bytes(target)).hexdigest() != item["header_sha256"]:
            raise SafetyError("Destination verification failed; reconcile, do not retry mutation")
        retained = set(item["flags"]) - {"\\Recent", "\\Deleted"}
        actual = set(flags(target)) - {"\\Recent"}
        if retained != actual or target.get(b"RFC822.SIZE") != item["size"]:
            raise SafetyError("Destination flags/size differ; reconcile before continuing")
    if not use_move:
        # Re-check source and validity after destination verification; refuse flag drift.
        if snapshot(client, folder, uidvalidity, uids) != current:
            raise SafetyError("Source changed after COPY; copied duplicates may exist, reconcile")
        select(client, folder, uidvalidity, readonly=False)
        client.delete_messages(uids, silent=True)
        client.uid_expunge(uids)
    return {"status": "moved", "source_folder": folder, "source_uidvalidity": uidvalidity,
            "destination": destination, "destination_uidvalidity": target_validity,
            "uid_mapping": {str(a): b for a, b in mapping.items()}, "method": "UID MOVE" if use_move else "UIDPLUS"}


def compose(account, to, subject, body, cc=None, bcc=None):
    """Plain text send only; no local file reads, arbitrary headers, or attachment paths."""
    destinations = []
    normalized = {}
    for field, values in (("to", to), ("cc", cc or []), ("bcc", bcc or [])):
        if not isinstance(values, list) or len(values) > 50:
            raise SafetyError("Recipients must be bounded explicit address lists")
        for value in values:
            if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
                raise SafetyError("Invalid recipient")
            parsed = getaddresses([value])
            if len(parsed) != 1 or parsed[0][1] != value or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
                raise SafetyError("Use bare explicit email addresses")
        normalized[field] = list(values)
        destinations.extend(values)
    if not normalized["to"] or len(destinations) > 50:
        raise SafetyError("Require To recipient and at most 50 total recipients")
    if not isinstance(subject, str) or len(subject) > 998 or any(c in subject for c in "\r\n\x00"):
        raise SafetyError("Invalid subject")
    if not isinstance(body, str) or len(body.encode()) > 100000:
        raise SafetyError("Plain text body exceeds limit")
    return {"account": account, **normalized, "subject": subject, "body": body}


def mime_message(payload, plan_id):
    msg = EmailMessage(policy=policy.SMTP)
    msg["From"] = payload["account"]
    msg["To"] = ", ".join(payload["to"])
    if payload["cc"]:
        msg["Cc"] = ", ".join(payload["cc"])
    msg["Subject"] = payload["subject"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(idstring=plan_id)
    msg.set_content(payload["body"])
    return msg
