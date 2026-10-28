"""Exercise the real MCP handler with a synthetic uncertain reminder."""
import json, os, select, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def run():
    with tempfile.TemporaryDirectory(prefix='reminder-observation-') as directory:
        home = Path(directory)
        env = {'HOME': directory, 'PATH': os.defpath, 'PYTHONPATH': str(ROOT/'src') + os.pathsep + str(ROOT/'tests'),
               'PYTHONUNBUFFERED': '1', 'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off',
               'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com', 'ICLOUD_MAIL_ACCESS': 'read-only',
               'ICLOUD_APPROVAL_POLICY': 'trusted-client', 'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '0',
               'ICLOUD_ENABLE_CONTACTS': '0', 'ICLOUD_ENABLE_NOTES': '0', 'ICLOUD_ENABLE_REMINDERS': '1',
               'ICLOUD_NATIVE_CONFIG': json.dumps({'reminders': {'source_ids': ['selected'], 'access_mode': 'read-only'}}),
               'ICLOUD_RUNTIME_ROOT': str(home/'runtime'), 'BRIDGE_TEST_ORIGINAL': str(home/'original.json')}
        with (home/'stderr').open('wb') as stderr:
            process = subprocess.Popen([sys.executable, str(ROOT/'tests/native_reminder_observation_stdio_server.py')],
                cwd=directory, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
            sequence = 0
            def rpc(method, params):
                nonlocal sequence
                sequence += 1; current = sequence
                process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': current, 'method': method, 'params': params})+'\n').encode()); process.stdin.flush()
                deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], 1)[0]:
                        line = process.stdout.readline()
                        if not line: raise AssertionError('Synthetic MCP exited')
                        value = json.loads(line)
                        if value.get('id') == current: return value
                raise AssertionError('Synthetic MCP timed out')
            try:
                rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'synthetic', 'version': '1'}})
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); process.stdin.flush()
                identity = json.loads((home/'original.json').read_text())['plan_id']
                result = rpc('tools/call', {'name': 'reminders_inspect_uncertain_change', 'arguments': {'plan_id': identity}})['result']
                assert not result.get('isError'), result
                value = result.get('structuredContent') or json.loads(result['content'][0]['text'])
                assert value['observed_snapshot_matches_before'] is True and value['original_uncertainty_retained'] is True
                assert value['causal_dispatch_proven'] is False and value['data_mutated'] is False
                assert 'Synthetic' not in json.dumps(value)
                refused = rpc('tools/call', {'name': 'reminders_execute_approved', 'arguments': {'plan_id': identity}})['result']
                assert refused.get('isError')
                def call(name,arguments):return rpc('tools/call',{'name':name,'arguments':arguments})['result']
                def data(result):
                    assert not result.get('isError'),result
                    return result.get('structuredContent') or json.loads(result['content'][0]['text'])
                assert call('reminders_prepare_observation_recovery',{'plan_id':identity}).get('isError')
                review=data(call('reminders_prepare_observation_recovery',{'plan_id':identity,'legacy_missing_terminal':True}))
                assert 'No original request' in review['review']['payload']['effects']['missing_terminal_evidence']
                assert call('reminders_accept_observation_recovery',{'plan_id':review['plan_id']}).get('isError')
                acknowledgement=review['review']['payload']['legacy_acknowledgement']
                accepted=data(call('reminders_accept_observation_recovery',{'plan_id':review['plan_id'],'owner_acknowledgement':acknowledgement}))
                assert accepted['causal_dispatch_proven'] is False and accepted['native_data_changed'] is False
                status=data(call('reminders_plan_status',{'plan_id':identity}))
                assert status['original_status']=='failed_or_uncertain' and status['do_not_retry'] is True
                assert call('reminders_execute_approved',{'plan_id':identity}).get('isError')
            finally:
                process.stdin.close()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
                process.stdout.close()
        assert process.returncode == 0, (home/'stderr').read_text()
    print(json.dumps({'status': 'passed', 'real_mcp_stdio': True, 'synthetic_provider_only': True, 'uncertainty_retained': True}))

if __name__ == '__main__': run()
