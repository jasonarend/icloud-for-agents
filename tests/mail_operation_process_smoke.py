"""Real-process ordinary-Mail admission and crash recovery with synthetic effects."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.core import digest
from icloud_for_agents.mail_operations import MailOperations
from test_mail_operations import RecoveryIMAP, ACCOUNT

ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import os,sys,json
from pathlib import Path
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.mail_operations import MailOperations
from icloud_for_agents.core import snapshot
from test_mail_operations import RecoveryIMAP,ACCOUNT
root,identity,mode=sys.argv[1:]
operations=MailOperations(ACCOUNT,PlanStore(Path(root)))
client=RecoveryIMAP()
intent={'folder':'INBOX','uidvalidity':71,'uids':[1],'seen':True}
before=snapshot(client,'INBOX',71,[1])
def action(progress):
 progress('dispatch',{'method':'UID STORE','seen':True})
 with open(Path(root)/'synthetic-effects','a') as f:f.write(identity+'\n');f.flush();os.fsync(f.fileno())
 print('entered',flush=True)
 if mode=='crash':os._exit(17)
 if mode=='wait':sys.stdin.readline()
 return {'status':'completed_synthetic'}
try:print(json.dumps(operations.run('set_seen',intent,before,action,identity)),flush=True)
except Exception as exc:print('refused: '+str(exc),flush=True);sys.exit(3)
'''


class MailOperationProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mail-operation-process-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runtime'
        self.store = PlanStore(self.root)
        self.operations = MailOperations(ACCOUNT, self.store)
        self.env = {'HOME': self.temp.name, 'PATH': os.defpath,
                    'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT / 'tests'),
                    'ICLOUD_MAIL_ACCOUNT': ACCOUNT, 'ICLOUD_APPROVAL_POLICY': 'local-receipt'}

    def child(self, identity, mode='normal'):
        process = subprocess.Popen([sys.executable, '-c', CHILD, str(self.root), identity, mode],
                                   env=self.env, cwd=self.temp.name, text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)

    def test_competing_process_cannot_dispatch_or_consume_a_second_operation(self):
        writer = self.child('a' * 32, 'wait')
        self.assertEqual(writer.stdout.readline().strip(), 'entered')
        blocked = self.child('b' * 32)
        output, errors = blocked.communicate(timeout=15)
        self.assertEqual(blocked.returncode, 3, errors)
        self.assertIn('Another process', output)
        self.assertFalse(self.operations.store.path('b' * 32, '.started').exists())
        writer.stdin.write('finish\n'); writer.stdin.flush()
        writer.communicate(timeout=15)
        self.assertEqual(self.operations.status('a' * 32)['status'], 'completed')

    def test_crash_releases_lock_but_same_id_does_not_repeat_effect(self):
        crashed = self.child('a' * 32, 'crash')
        crashed.communicate(timeout=15)
        self.assertEqual(crashed.returncode, 17)
        before = (self.root / 'synthetic-effects').read_bytes()
        replay = self.child('a' * 32)
        output, errors = replay.communicate(timeout=15)
        self.assertEqual(replay.returncode, 0, errors)
        self.assertIn('started_or_uncertain', output)
        self.assertEqual(before, (self.root / 'synthetic-effects').read_bytes())
        blocked = self.child('b' * 32)
        output, errors = blocked.communicate(timeout=15)
        self.assertEqual(blocked.returncode, 3, errors)
        self.assertIn('unfinished or uncertain', output)

    def test_separately_reviewed_recovery_preserves_crash_evidence_and_allows_new_id(self):
        crashed = self.child('a' * 32, 'crash')
        crashed.communicate(timeout=15)
        start = self.operations.store.path('a' * 32, '.started').read_bytes()
        prepared = self.operations.prepare_recovery(RecoveryIMAP(), 'a' * 32, 'accept_uncertainty', {})
        self.store.write_once(self.store.path(prepared['plan_id'], '.approved'),
                              {'plan_id': prepared['plan_id'], 'sha256': digest(prepared['review'])})
        result = self.operations.execute_recovery(RecoveryIMAP(), prepared['plan_id'], {})
        self.assertEqual(result['recovery_status'], 'owner_accepted_uncertainty')
        self.assertEqual(start, self.operations.store.path('a' * 32, '.started').read_bytes())
        self.assertFalse(self.operations.store.path('a' * 32, '.result').exists())
        followup = self.child('b' * 32)
        output, errors = followup.communicate(timeout=15)
        self.assertEqual(followup.returncode, 0, errors)
        self.assertEqual((self.root / 'synthetic-effects').read_text().splitlines(), ['a' * 32, 'b' * 32])


if __name__ == '__main__':
    unittest.main()
