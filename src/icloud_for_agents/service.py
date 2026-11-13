"""Runtime transport adapters. Imports/connects third-party IMAP client only on use."""

from contextlib import contextmanager
import os
import smtplib
import ssl

from . import core
from .approvals import PlanStore
from .cleanup_access import configured as cleanup_access
from .mail_operations import MailOperations


class MailService:
    def __init__(self, account, password, store=None, overrides=None, connector=None):
        self.account = account
        self._password = password
        self.store = store if store is not None else PlanStore()
        self.operations = MailOperations(account, self.store)
        self.overrides = overrides if overrides is not None else {
            role: os.environ[key] for role, key in (("Trash", "ICLOUD_TRASH_FOLDER"),
                                                   ("Archive", "ICLOUD_ARCHIVE_FOLDER"),
                                                   ("Sent", "ICLOUD_SENT_FOLDER")) if key in os.environ}
        self._connector = connector

    @contextmanager
    def imap(self):
        if self._connector:
            client = self._connector()
        else:
            from .imap_transport import CompatibleIMAPClient
            client = CompatibleIMAPClient("imap.mail.me.com", port=993, ssl=True, use_uid=True,
                                ssl_context=ssl.create_default_context(), timeout=30)
            try:
                client.login(self.account, self._password)
            except Exception:
                client.shutdown()
                raise core.SafetyError("iCloud IMAP authentication failed; check local credential handoff") from None
        try:
            yield client
        finally:
            # LOGOUT does not EXPUNGE; never use CLOSE or an unscoped expunge.
            try:
                client.logout()
            except Exception:
                client.shutdown()

    def list_folders(self):
        with self.imap() as client:
            return core.folders(client)

    def page(self, **kwargs):
        with self.imap() as client:
            return core.page(client, self.account, **kwargs)

    def inspect(self, **kwargs):
        with self.imap() as client:
            return core.inspect(client, **kwargs)

    def set_seen(self, **kwargs):
        identity = kwargs.pop("operation_id", None)
        kwargs["uids"] = core.batch(kwargs["uids"])
        intent = {key: kwargs[key] for key in ("folder", "uidvalidity", "uids", "seen")}
        previous = self.operations.existing(identity, "set_seen", intent)
        if previous is not None:
            return previous
        with self.operations.locked():
            self.operations.assert_settled()
            access=cleanup_access(self.account)
            if access and identity is not None:
                raise core.SafetyError("Standing cleanup uses its own durable action identity; omit operation_id")
            if access and kwargs.get("seen") is not True:
                raise core.SafetyError("Standing cleanup only permits marking read; no unattended mark-unread")
            if access:access.check("mark_read",kwargs["folder"],kwargs["uids"])
            with self.imap() as client:
                items=core.snapshot(client,kwargs["folder"],kwargs["uidvalidity"],kwargs["uids"])
                if not access:
                    return self.operations.run("set_seen", intent, items,
                        lambda progress: core.set_seen(client, expected_snapshot=items, progress=progress, **kwargs), identity)
                return access.run("mark_read",kwargs["folder"],kwargs["uidvalidity"],kwargs["uids"],None,items,
                                  lambda:core.set_seen(client,expected_snapshot=items,**kwargs))

    def prepare_trash(self, folder, uidvalidity, uids, reason):
        if not isinstance(reason, str) or not 1 <= len(reason) <= 2000:
            raise core.SafetyError("Supply a bounded explanation for the specific cleanup batch")
        with self.imap() as client:
            trash = core.role_folder(client, "Trash", self.overrides)
            if folder == trash:
                raise core.SafetyError("Already in Trash: permanent purge is disabled")
            items = core.snapshot(client, folder, uidvalidity, uids)
            payload = {"account": self.account, "folder": folder, "uidvalidity": uidvalidity,
                       "destination": trash, "items": items, "reason": reason}
        return self.store.prepare("trash", payload)

    def archive(self, folder, uidvalidity, uids, operation_id=None):
        uids = core.batch(uids)
        intent = {"folder": folder, "uidvalidity": uidvalidity, "uids": uids}
        previous = self.operations.existing(operation_id, "archive", intent)
        if previous is not None:
            return previous
        with self.operations.locked():
            self.operations.assert_settled()
            with self.imap() as client:
                archive = core.role_folder(client, "Archive", self.overrides)
                trash = core.role_folder(client, "Trash", self.overrides)
                if folder == trash or archive == trash:
                    raise core.SafetyError("Use explicit restore for messages in Trash")
                access=cleanup_access(self.account)
                if access and operation_id is not None:
                    raise core.SafetyError("Standing cleanup uses its own durable action identity; omit operation_id")
                if access:access.check("archive",folder,uids,archive)
                items = core.snapshot(client, folder, uidvalidity, uids)
                if access:return access.run("archive",folder,uidvalidity,uids,archive,items,
                                            lambda:core.safe_move(client,folder,uidvalidity,uids,archive,items))
                return self.operations.run("archive", intent, items,
                    lambda progress: core.safe_move(client, folder, uidvalidity, uids, archive, items, progress=progress),
                    operation_id, archive)

    def restore(self, trash_uidvalidity, uids, destination, operation_id=None):
        if cleanup_access(self.account):
            raise core.SafetyError("Restore is outside unattended cleanup; use a separately reviewed owner workflow")
        uids = core.batch(uids)
        # The source is resolved from the configured Trash role for a new request;
        # an existing ID is inspected without reconnecting or replaying a move.
        if operation_id is not None and self.operations.store.path(operation_id, ".json").exists():
            record = self.operations.read(operation_id)
            intent = dict(record["intent"], uidvalidity=trash_uidvalidity, uids=uids, destination=destination)
            return self.operations.existing(operation_id, "restore", intent)
        with self.operations.locked():
            self.operations.assert_settled()
            with self.imap() as client:
                trash = core.role_folder(client, "Trash", self.overrides)
                core.exact_folder(client, destination)
                intent = {"folder": trash, "uidvalidity": trash_uidvalidity, "uids": uids, "destination": destination}
                items = core.snapshot(client, trash, trash_uidvalidity, uids)
                return self.operations.run("restore", intent, items,
                    lambda progress: core.safe_move(client, trash, trash_uidvalidity, uids, destination, items, progress=progress),
                    operation_id, destination)

    def observe_operation(self, operation_id):
        with self.imap() as client:
            return self.operations.observe_recorded(client, operation_id)

    def prepare_operation_recovery(self, operation_id, action):
        with self.imap() as client:
            return self.operations.prepare_recovery(client, operation_id, action, self.overrides)

    def execute_operation_recovery(self, plan_id):
        with self.imap() as client:
            return self.operations.execute_recovery(client, plan_id, self.overrides)

    def prepare_send(self, to, subject, body, cc=None, bcc=None):
        payload = core.compose(self.account, to, subject, body, cc, bcc)
        payload["message_id"] = core.new_message_id()
        with self.imap() as client:
            payload["sent_folder"] = core.role_folder(client, "Sent", self.overrides)
        return self.store.prepare("send", payload)

    def execute(self, plan_id):
        with self.operations.locked():
            self.operations.assert_settled()
            return self._execute(plan_id)

    def _execute(self, plan_id):
        candidate = self.store.proposal(plan_id)
        if candidate['kind'] not in {'trash', 'send'}:
            raise core.SafetyError('Not a Mail proposal; use the matching service executor')
        access=cleanup_access(self.account)
        if access and candidate["kind"] == "trash":
            return self._standing_trash(access,candidate)
        plan = self.store.consume(plan_id, self.account)
        payload = plan["payload"]
        try:
            if plan["kind"] == "trash":
                with self.imap() as client:
                    if core.role_folder(client, "Trash", self.overrides) != payload["destination"]:
                        raise core.SafetyError("Trash folder changed since approval")
                    result = core.safe_move(client, payload["folder"], payload["uidvalidity"],
                                            [item["uid"] for item in payload["items"]],
                                            payload["destination"], payload["items"])
            else:
                result = self._send(payload, plan_id)
        except Exception as exc:
            # Network errors can include credentials or SMTP content. Never return/log
            # raw exceptions. Result is deliberately conservative, even preflight failure.
            result = {"status": "failed_or_uncertain", "do_not_retry": True,
                      "message": "Inspect source/destination or Sent before preparing any replacement plan"}
            if isinstance(exc, core.SafetyError):
                result["safety_reason"] = str(exc)
        return self.store.finish(plan_id, result)

    def _standing_trash(self, access, candidate):
        payload=candidate["payload"];plan_id=candidate["plan_id"]
        if payload.get("account")!=self.account:raise core.SafetyError("Cleanup proposal belongs to another account")
        uids=[item["uid"] for item in payload["items"]]
        access.check("trash",payload["folder"],uids,payload["destination"])
        with self.imap() as client:
            if core.role_folder(client,"Trash",self.overrides)!=payload["destination"]:
                raise core.SafetyError("Exact Trash destination changed; no standing write")
            def action():
                self.store.write_once(self.store.path(plan_id,".started"),
                                      {"started":self.store.clock(),"authorization":"standing_cleanup","grant_id":access.grant_id})
                return core.safe_move(client,payload["folder"],payload["uidvalidity"],uids,
                                      payload["destination"],payload["items"])
            try:
                result=access.run("trash",payload["folder"],payload["uidvalidity"],uids,payload["destination"],payload["items"],action)
            except core.SafetyError:
                if self.store.path(plan_id,".started").exists():
                    self.store.finish(plan_id,{"status":"failed_or_uncertain","do_not_retry":True})
                raise
        return self.store.finish(plan_id,result)

    def _send(self, payload, plan_id):
        # Revalidate stored content; no unreviewed recipient/header changes.
        core.compose(self.account, payload["to"], payload["subject"], payload["body"], payload["cc"], payload["bcc"])
        with self.imap() as client:
            if core.role_folder(client, "Sent", self.overrides) != payload["sent_folder"]:
                raise core.SafetyError("Sent folder changed since review")
        message = core.mime_message(payload)
        recipients = payload["to"] + payload["cc"] + payload["bcc"]
        with smtplib.SMTP("smtp.mail.me.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(self.account, self._password)
            refused = smtp.send_message(message, from_addr=self.account, to_addrs=recipients)
        # SMTP has already accepted delivery: failed Sent append must never trigger resend.
        result = {"status": "sent" if not refused else "partially_sent", "do_not_retry": True,
                  "message_id": str(message["Message-ID"]), "refused_recipients": list(refused),
                  "sent_copy": "unconfirmed"}
        try:
            with self.imap() as client:
                core.exact_folder(client, payload["sent_folder"])
                client.append(payload["sent_folder"], message.as_bytes(), flags=["\\Seen"])
            result["sent_copy"] = "saved"
        except Exception:
            pass
        return result
