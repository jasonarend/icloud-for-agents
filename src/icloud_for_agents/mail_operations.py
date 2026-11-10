"""Durable ordinary Mail operations and exact, locally reviewed recovery.

An observation is current provider state, never proof of which request caused it.
Original start/result records remain immutable after recovery.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import threading
import uuid

from . import core
from .approvals import PlanStore
from .core import SafetyError, digest


OPERATIONS = frozenset({"set_seen", "archive", "restore"})
PHASES = frozenset({"dispatch", "mapping", "destination_verified", "source_removal"})
_REGISTRY_LOCK = threading.Lock()
_LOCKS = {}


class MailOperations:
    def __init__(self, account, store=None):
        self.account = account
        self.approvals = store if store is not None else PlanStore()
        self.store = PlanStore(self.approvals.root / ("mail-operations-" + hashlib.sha256(account.encode()).hexdigest()[:32]), clock=self.approvals.clock)
        with _REGISTRY_LOCK:
            self._thread_lock, self._local = _LOCKS.setdefault(str(self.store.root.resolve()),
                                                           (threading.RLock(), threading.local()))

    @contextmanager
    def locked(self):
        with self._thread_lock:
            if getattr(self._local, "depth", 0):
                self._local.depth += 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            self.approvals.ready()
            self.store.ready()
            fd = os.open(self.store.root / "execution.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise SafetyError("Invalid ordinary Mail operation lock")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SafetyError("Another process is executing a Mail operation; inspect its status") from None
                self._local.depth = 1
                try:
                    yield
                finally:
                    self._local.depth = 0
            finally:
                os.close(fd)

    def read(self, identity):
        record = self.store.read(self.store.path(identity, ".json"))
        if (not isinstance(record, dict) or record.get("operation_id") != identity
                or record.get("account") != self.account or record.get("operation") not in OPERATIONS
                or not isinstance(record.get("intent"), dict) or not isinstance(record.get("before"), list)):
            raise SafetyError("Mail operation belongs to another account or has invalid evidence")
        return record

    def _started(self, identity, record):
        start = self.store.read(self.store.path(identity, ".started"))
        if start != {"operation_sha256": digest(record), "account": self.account}:
            raise SafetyError("Mail operation start does not match its immutable request")

    def status(self, identity):
        record = self.read(identity)
        self._started(identity, record)
        result_path = self.store.path(identity, ".result")
        result = self.store.read(result_path) if result_path.exists() else {"status": "started_or_uncertain"}
        if not isinstance(result, dict) or result.get("status") not in {"completed", "refused_without_dispatch", "failed_or_uncertain", "started_or_uncertain"}:
            raise SafetyError("Invalid ordinary Mail result")
        value = {"operation_id": identity, "operation": record["operation"], "intent": record["intent"],
                 "created": record["created"], "status": result["status"], "do_not_retry": True,
                 "mutation_replayed": False}
        if result.get("status") == "completed":
            value["result"] = result["result"]
        settled = self.store.path(identity, ".settled")
        if settled.exists():
            evidence = self.settlement(identity)
            value.update(status=evidence["status"], original_status=result["status"],
                         original_uncertainty_retained=True, causal_dispatch_proven=False)
        return value

    def settlement(self, identity):
        record = self.read(identity)
        evidence = self.store.read(self.store.path(identity, ".settled"))
        if (evidence.get("operation_sha256") != digest(record)
                or evidence.get("status") not in {"owner_accepted_uncertainty", "verified_copy_completion"}):
            raise SafetyError("Invalid ordinary Mail recovery evidence")
        plan_id = evidence.get("recovery_plan_id")
        plan = self.approvals.read(self.approvals.path(plan_id, ".json"))
        started = self.approvals.read(self.approvals.path(plan_id, ".started"))
        receipt = self.approvals.read(self.approvals.path(plan_id, ".approved"))
        result = self.approvals.read(self.approvals.path(plan_id, ".result"))
        payload = plan.get("payload", {})
        if (plan.get("kind") != "mail_recovery" or payload.get("account") != self.account
                or payload.get("operation_id") != identity or payload.get("operation_sha256") != digest(record)
                or started.get("sha256") != digest(plan) or started.get("authorization") != "local-receipt"
                or receipt.get("sha256") != digest(plan) or receipt.get("plan_id") != plan_id
                or result.get("status") != "completed" or result.get("recovery_status") != evidence["status"]):
            raise SafetyError("Mail settlement is not bound to a completed exact owner review")
        return evidence

    def recovery_settled(self, plan_id):
        plan = self.approvals.read(self.approvals.path(plan_id, ".json"))
        if plan.get("kind") != "mail_recovery" or plan.get("payload", {}).get("account") != self.account:
            return False
        if not self.approvals.path(plan_id, ".started").exists():
            return False
        started = self.approvals.read(self.approvals.path(plan_id, ".started"))
        if started.get("sha256") != digest(plan):
            return False
        identity = plan["payload"]["operation_id"]
        if not self.store.path(identity, ".settled").exists():
            return False
        return self.settlement(identity)["operation_sha256"] == plan["payload"].get("operation_sha256")

    def existing(self, identity, operation, intent):
        if identity is None:
            return None
        path = self.store.path(identity, ".json")
        if not path.exists():
            return None
        record = self.read(identity)
        if record["operation"] != operation or record["intent"] != intent:
            raise SafetyError("Mail operation ID belongs to a different request")
        if not self.store.path(identity, ".started").exists():
            return {"operation_id": identity, "status": "not_dispatched", "do_not_retry": True,
                    "mutation_replayed": False}
        return self.status(identity)

    def _files(self, suffix):
        self.approvals.ready()
        self.store.ready()
        names = []
        for entry in self.store.root.iterdir():
            if entry.name.endswith(suffix):
                names.append(entry)
                if len(names) > 10000:
                    raise SafetyError("Mail operation inventory exceeds its bounded scan; inspect an exact operation")
        return sorted(names)

    def assert_ordinary_settled(self):
        for path in self._files(".started"):
            identity = path.stem
            record = self.read(identity)
            if record["account"] != self.account:
                continue
            if self.status(identity)["status"] in {"started_or_uncertain", "failed_or_uncertain"}:
                raise SafetyError("A prior Mail operation is unfinished or uncertain; inspect and review its recovery before another write")

    def assert_settled(self):
        """Called under the outer Mail lock, before admitting a new data write.

        Lock order is MCP invocation -> ordinary Mail -> managed policy -> cleanup
        ledger -> approval admission. Read-only observations/recovery do not call
        this check. Cleanup/managed executors check ordinary records before their
        own locks so they do not recursively reject their own active journal.
        """
        self.assert_ordinary_settled()
        for path in self.approvals.root.glob('*.started'):
            plan = self.approvals.read(self.approvals.path(path.stem, '.json'))
            if (plan.get('kind') in {'send', 'trash'} and plan.get('payload', {}).get('account') == self.account
                    and not self.approvals.operation_settled(path.stem)):
                raise SafetyError('An approved Mail operation remains uncertain; reconcile it before another ordinary write')
        cleanup = PlanStore(self.approvals.root / 'cleanup-audit', clock=self.approvals.clock)
        if cleanup.root.exists():
            cleanup.ready()
            for path in cleanup.root.glob('*.started'):
                entry = cleanup.read(path)
                result = path.with_suffix('.result')
                if (entry.get('account') == self.account
                        and (not result.exists() or cleanup.read(result).get('status') != 'completed')):
                    raise SafetyError('Standing cleanup remains uncertain even if its grant is disabled; reconcile before another ordinary write')
        managed = PlanStore(self.approvals.root / 'managed-policy', clock=self.approvals.clock)
        if managed.root.exists():
            managed.ready()
            binding = managed.root / 'binding.json'
            if binding.exists() and managed.read(binding).get('account') == self.account:
                for path in managed.root.glob('*.started'):
                    result = path.with_suffix('.result')
                    if not result.exists() or managed.read(result).get('status') != 'completed':
                        raise SafetyError('Managed Mail execution remains uncertain; reconcile before another ordinary write')

    def list(self, limit=20, after=None, outstanding_only=True):
        if type(limit) is not int or not 1 <= limit <= 100 or type(outstanding_only) is not bool:
            raise SafetyError("Use a bounded Mail operation page")
        if after is not None:
            self.store.path(after, ".json")
        values = []
        candidates = [path.stem for path in self._files(".started") if after is None or path.stem > after]
        for identity in candidates:
            status = self.status(identity)
            if not outstanding_only or status["status"] in {"started_or_uncertain", "failed_or_uncertain"}:
                values.append(status)
            if len(values) == limit:
                return {"operations": values, "next_cursor": identity, "done": identity == candidates[-1]}
        return {"operations": values, "next_cursor": None, "done": True}

    def run(self, operation, intent, before, action, identity=None, destination=None):
        if operation not in OPERATIONS:
            raise SafetyError("Unsupported ordinary Mail operation")
        with self.locked():
            previous = self.existing(identity, operation, intent)
            if previous is not None:
                return previous
            self.assert_settled()
            identity = identity or uuid.uuid4().hex
            record = {"operation_id": identity, "account": self.account, "operation": operation,
                      "intent": intent, "before": before, "destination": destination,
                      "created": self.store.clock()}
            self.store.write_once(self.store.path(identity, ".json"), record)
            self.store.write_once(self.store.path(identity, ".started"),
                                  {"operation_sha256": digest(record), "account": self.account})

            def progress(phase, data):
                if phase not in PHASES:
                    raise SafetyError("Invalid Mail operation progress phase")
                self.store.write_once(self.store.path(identity, ".phase-" + phase),
                                      {"operation_sha256": digest(record), "data": data})

            try:
                result = action(progress)
            except Exception as exc:
                dispatched = self.store.path(identity, ".phase-dispatch").exists()
                self.store.write_once(self.store.path(identity, ".result"),
                                      {"status": "failed_or_uncertain" if dispatched else "refused_without_dispatch", "do_not_retry": True})
                if not dispatched and isinstance(exc, SafetyError):
                    raise
                raise SafetyError("Mail operation outcome uncertain; inspect operation " + identity + "; do not replay") from None
            self.store.write_once(self.store.path(identity, ".result"), {"status": "completed", "result": result})
            return dict(result, operation_id=identity, mutation_replayed=False)

    def phase(self, identity, phase):
        record = self.read(identity)
        path = self.store.path(identity, ".phase-" + phase)
        if not path.exists():
            return None
        value = self.store.read(path)
        if value.get("operation_sha256") != digest(record):
            raise SafetyError("Mail progress does not bind the original operation")
        return value["data"]

    @staticmethod
    def _metadata(client, folder, validity, uids):
        core.select(client, folder, validity, readonly=True)
        rows = client.fetch(uids, ["FLAGS", "RFC822.SIZE", core.HEADER_FETCH])
        return {str(n): {"flags": sorted(set(core.flags(row)) - {"\\Recent"}),
                         "size": row.get(b"RFC822.SIZE"),
                         "header_sha256": hashlib.sha256(core.body_bytes(row)).hexdigest()}
                for n, row in rows.items() if n in uids}

    def observe(self, client, identity):
        record = self.read(identity)
        self._started(identity, record)
        intent = record["intent"]
        source = self._metadata(client, intent["folder"], intent["uidvalidity"], intent["uids"])
        observation = {"source": source, "destination": None, "desired_state_observed": False,
                       "causal_dispatch_proven": False}
        before = {str(item["uid"]): {"flags": sorted(set(item["flags"]) - {"\\Recent"}),
                                    "size": item["size"], "header_sha256": item["header_sha256"]}
                  for item in record["before"]}
        if record["operation"] == "set_seen":
            expected = {key: dict(value, flags=sorted((set(value["flags"]) | {"\\Seen"}) if intent["seen"]
                                else (set(value["flags"]) - {"\\Seen"}))) for key, value in before.items()}
            observation["desired_state_observed"] = source == expected
        else:
            mapping = self.phase(identity, "mapping")
            if mapping is not None:
                target_uids = list(mapping["uid_mapping"].values())
                target = self._metadata(client, record["destination"], mapping["destination_uidvalidity"], target_uids)
                observation["destination"] = target
                expected = {str(mapping["uid_mapping"][key]): value for key, value in before.items()}
                observation["desired_state_observed"] = not source and target == expected
            else:
                observation["destination_identity_unknown"] = True
        return observation

    def observe_recorded(self, client, identity):
        with self.locked():
            observed = self.observe(client, identity)
            observation_id = uuid.uuid4().hex
            self.store.write_once(self.store.path(identity, ".observation-" + observation_id),
                                  {"operation_sha256": digest(self.read(identity)), "observation": observed,
                                   "observed_at": self.store.clock()})
            return {"operation_id": identity, "observation_id": observation_id,
                    "observation": observed, "original_uncertainty_retained": True, "mutation_attempted": False}

    def prepare_recovery(self, client, identity, action, overrides):
        if action not in {"accept_uncertainty", "complete_verified_copy"}:
            raise SafetyError("Choose an exact supported Mail recovery action")
        with self.locked():
            record = self.read(identity)
            if self.status(identity)["status"] not in {"started_or_uncertain", "failed_or_uncertain"}:
                raise SafetyError("Mail operation does not require recovery")
            observation = self.observe(client, identity)
            if action == "complete_verified_copy":
                from .reconcile import mail_copy_evidence
                observation["copy_evidence"] = mail_copy_evidence(client, record, self.phase(identity, "mapping"), overrides)
            payload = {"account": self.account, "operation_id": identity, "operation_sha256": digest(record),
                       "action": action, "observation": observation, "intent": record["intent"],
                       "warning": ("Accept possible delayed or unobserved effects and permit fresh separately authorized work without replay. The original failure remains uncertain."
                                   if action == "accept_uncertainty" else
                                   "Retain each exact verified destination copy and remove only its matching source. Never create another copy. The original failure remains recorded.")}
            return self.approvals.prepare("mail_recovery", payload)

    def execute_recovery(self, client, plan_id, overrides):
        with self.locked():
            plan = self.approvals.proposal(plan_id)
            payload = plan.get("payload", {})
            if plan.get("kind") != "mail_recovery" or payload.get("account") != self.account:
                raise SafetyError("Not an exact Mail recovery proposal")
            identity = payload["operation_id"]
            record = self.read(identity)
            if payload.get("operation_sha256") != digest(record):
                raise SafetyError("Recovery no longer binds the original Mail operation")
            if self.status(identity)["status"] not in {"started_or_uncertain", "failed_or_uncertain"}:
                raise SafetyError("The Mail operation has already been settled")
            observation = self.observe(client, identity)
            if payload["action"] == "complete_verified_copy":
                from .reconcile import mail_copy_evidence
                observation["copy_evidence"] = mail_copy_evidence(client, record, self.phase(identity, "mapping"), overrides)
            if observation != payload.get("observation"):
                raise SafetyError("Mail recovery observation changed; prepare and review a fresh proposal")
            self.approvals.consume(plan_id, self.account)
            try:
                if payload["action"] == "complete_verified_copy":
                    from .reconcile import complete_mail_copy
                    complete_mail_copy(client, record, self.phase(identity, "mapping"), observation["copy_evidence"], overrides)
                    if not self.observe(client, identity)["desired_state_observed"]:
                        raise SafetyError("Mail copy completion is unconfirmed")
                    status = "verified_copy_completion"
                elif payload["action"] == "accept_uncertainty":
                    status = "owner_accepted_uncertainty"
                else:
                    raise SafetyError("Unsupported Mail recovery action")
                result = self.approvals.finish(plan_id, {"status": "completed", "recovery_status": status, "operation_id": identity,
                     "original_uncertainty_retained": True, "causal_dispatch_proven": False,
                     "mutation_attempted": payload["action"] == "complete_verified_copy", "do_not_retry": True})
                self.store.write_once(self.store.path(identity, ".settled"),
                                      {"status": status, "operation_sha256": digest(record), "recovery_plan_id": plan_id})
                return result
            except Exception:
                if self.approvals.path(plan_id, ".result").exists():
                    raise SafetyError("Recovery completed but settlement recording is unconfirmed; inspect status before further work") from None
                return self.approvals.finish(plan_id, {"status": "failed_or_uncertain", "operation_id": identity,
                     "original_uncertainty_retained": True, "do_not_retry": True})
