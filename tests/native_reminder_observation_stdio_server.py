"""Real MCP server with one synthetic uncertain reminder and no native access."""
import json, os, sys
from pathlib import Path
sys.addaudithook(lambda event, args: (_ for _ in ()).throw(AssertionError('No network or helper'))
                 if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen'} else None)
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.native_changes import NativeChanges
from icloud_for_agents import native_tools
from test_native_reminder_observation import Adapter

store = PlanStore(); adapter = Adapter(); account = 'synthetic@example.com'
engine = NativeChanges('reminders', account, adapter, store)
item = {'operation': 'set_urgent', 'source_id': 'selected', 'id': 'reminder',
        'fields': {'urgent': True}, 'before': adapter.before}
plan = store.prepare('reminders_change', {'account': account, 'context': engine.context(True), 'changes': [item]})
store.consume(plan['plan_id'], account)
store.finish(plan['plan_id'], {'status': 'failed_or_uncertain', 'phase': 'mutation', 'attempted': [], 'completed': [], 'do_not_retry': True})
Path(os.environ['BRIDGE_TEST_ORIGINAL']).write_text(json.dumps({'plan_id': plan['plan_id']}))
native_tools.editor = lambda name, write=False: engine
from icloud_for_agents import native_reminder_recovery
recovery_class=native_reminder_recovery.ReminderObservationRecovery
native_reminder_recovery.ReminderObservationRecovery=lambda account:recovery_class(account,store,adapter)
from icloud_for_agents.server import main
main()
