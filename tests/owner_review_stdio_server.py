"""Synthetic strict-approval MCP target; no real windows, secrets or providers."""

import atexit
import json
import os
from pathlib import Path
import sys

blocked = []
shown = []


def audit(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen'}:
        blocked.append(event)
        raise RuntimeError('Synthetic owner review forbids network and child processes')


sys.addaudithook(audit)
atexit.register(lambda: Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps({
    'blocked_events': blocked, 'shown_plans': shown,
})))

from icloud_for_agents import credentials, owner_dialog
from icloud_for_agents.approvals import PLAN_KINDS, PlanStore
from icloud_for_agents.core import SafetyError, digest
from icloud_for_agents import managed_policy
from icloud_for_agents.policy_management import PolicyManager
from icloud_for_agents.services import enabled

ACCOUNT = 'synthetic@example.com'
store = PlanStore()
plans = {}


def no_credentials():
    blocked.append('credential_lookup')
    raise AssertionError('Owner review must not resolve credentials')


credentials.credentials = no_credentials

for kind in sorted(PLAN_KINDS):
    proposal = store.prepare(kind, {'account': ACCOUNT, 'synthetic_kind': kind,
                                    'effect': 'Synthetic review only; no execution requested'})
    plans[kind] = {'plan_id': proposal['plan_id'], 'sha256': proposal['sha256']}

foreign = store.prepare('calendar_change', {'account': 'other@example.com', 'effect': 'Foreign synthetic owner'})
declined = store.prepare('calendar_change', {'account': ACCOUNT, 'synthetic_review_decision': 'declined'})

manager = PolicyManager(store.root / 'synthetic-managed-policy')
policy = {'account': ACCOUNT, 'operations': ['mark_read'], 'source_folders': ['Synthetic'],
          'destinations': {}, 'max_batch': 2, 'max_24h': 5, 'expires': None, 'protect_starred': True}
manager.initialize_offline(policy)
policy_proposal = manager.preview_change(dict(policy, max_batch=1))
policy_id = policy_proposal['proposal']['plan_id']


def context(account):
    if (account != ACCOUNT or not enabled('mail')
            or os.environ.get('ICLOUD_ENABLE_POLICY_MANAGEMENT') != '1'):
        raise SafetyError('Synthetic managed-policy context refused')
    return manager, None, None


managed_policy.context = context


def local_decision(review, heading):
    # This fake belongs only to this test target. Production must show the
    # fixed local window and obtain an actual local dialog result.
    raw, fingerprint = review.rsplit('\n\nProposal SHA256: ', 1)
    proposal = json.loads(raw)
    if fingerprint != digest(proposal) or heading != 'Review this exact iCloud for Agents proposal':
        raise SafetyError('Synthetic local review was not bound to the complete proposal')
    shown.append({'plan_id': proposal['plan_id'], 'sha256': fingerprint})
    return proposal['payload'].get('synthetic_review_decision', 'approved')


owner_dialog.request_owner_decision = local_decision

manifest = {'plans': plans, 'foreign': foreign['plan_id'], 'declined': declined['plan_id'],
            'policy': {'plan_id': policy_id, 'sha256': policy_proposal['sha256']},
            'policy_before': manager.current(), 'runtime': str(store.root),
            'managed_runtime': str(manager.store.root)}
path = Path(os.environ['BRIDGE_TEST_MANIFEST'])
path.write_text(json.dumps(manifest))
path.chmod(0o600)

from icloud_for_agents import server

server.main()
