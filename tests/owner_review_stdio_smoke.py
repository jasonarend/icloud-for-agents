"""Exercise graphical receipt requests over real MCP stdio with synthetic UI."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters


ROOT = Path(__file__).resolve().parents[1]


def content(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


async def wait_review(client, plan_id, scope='service'):
    for _ in range(200):
        status = content(await client.call_tool('owner_review_status', {'plan_id': plan_id, 'scope': scope}))
        if status['status'] != 'pending':
            return status['status']
        await asyncio.sleep(0.01)
    raise AssertionError('Synthetic local review did not finish')


async def run(contacts):
    with tempfile.TemporaryDirectory(prefix='owner-review-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'BRIDGE_TEST_MANIFEST': str(home / 'manifest.json'),
               'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com', 'ICLOUD_MAIL_ACCESS': 'read-only',
               'ICLOUD_APPROVAL_POLICY': 'local-receipt', 'ICLOUD_ENABLE_POLICY_MANAGEMENT': '1',
               'ICLOUD_ENABLE_MAIL': '1', 'ICLOUD_ENABLE_CALENDAR': '1',
               'ICLOUD_ENABLE_CONTACTS': '1' if contacts else '0',
               'ICLOUD_ENABLE_NOTES': '1', 'ICLOUD_ENABLE_REMINDERS': '1',
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/owner_review_stdio_server.py')], env=env, cwd=home)
        expected_shown = set()
        async with Client(params, mode='auto', read_timeout_seconds=20) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools['owner_request_review'].annotations.read_only_hint is False
            assert tools['owner_review_status'].annotations.read_only_hint is True
            assert set(tools['owner_request_review'].input_schema['properties']) == {'plan_id', 'scope'}
            manifest = json.loads((home / 'manifest.json').read_text())
            runtime = Path(manifest['runtime'])
            before = {kind: (runtime / (item['plan_id'] + '.json')).read_bytes()
                      for kind, item in manifest['plans'].items()}
            for kind, item in manifest['plans'].items():
                plan_id = item['plan_id']
                receipt = runtime / (plan_id + '.approved')
                assert not receipt.exists()
                result = await client.call_tool('owner_request_review', {'plan_id': plan_id})
                if kind == 'contact_change' and not contacts:
                    assert result.is_error
                    assert not receipt.exists()
                    assert (await client.call_tool('owner_review_status', {'plan_id': plan_id})).is_error
                    continue
                assert content(result)['status'] in {'pending', 'approved'}
                assert await wait_review(client, plan_id) == 'approved'
                observed = json.loads(receipt.read_text())
                assert observed['plan_id'] == plan_id and observed['sha256'] == item['sha256']
                assert observed['authorization'] == 'local-owner-graphical-review'
                assert (runtime / (plan_id + '.json')).read_bytes() == before[kind]
                assert not (runtime / (plan_id + '.started')).exists()
                assert content(await client.call_tool('owner_request_review', {'plan_id': plan_id}))['status'] == 'approved'
                expected_shown.add(plan_id)

            foreign = manifest['foreign']
            assert (await client.call_tool('owner_request_review', {'plan_id': foreign})).is_error
            assert (await client.call_tool('owner_review_status', {'plan_id': foreign})).is_error
            assert not (runtime / (foreign + '.approved')).exists()
            declined = manifest['declined']
            content(await client.call_tool('owner_request_review', {'plan_id': declined}))
            assert await wait_review(client, declined) == 'declined'
            assert not (runtime / (declined + '.approved')).exists()
            expected_shown.add(declined)

            policy = manifest['policy']
            managed = Path(manifest['managed_runtime'])
            assert (await client.call_tool('owner_request_review', {'plan_id': policy['plan_id']})).is_error
            content(await client.call_tool('owner_request_review', {'plan_id': policy['plan_id'], 'scope': 'managed-policy'}))
            assert await wait_review(client, policy['plan_id'], 'managed-policy') == 'approved'
            receipt = json.loads((managed / (policy['plan_id'] + '.approved')).read_text())
            assert receipt['sha256'] == policy['sha256']
            assert receipt['authorization'] == 'local-owner-graphical-review'
            assert json.loads((managed / 'policy.json').read_text()) == manifest['policy_before']
            assert not (managed / (policy['plan_id'] + '.started')).exists()
            expected_shown.add(policy['plan_id'])

            assert (await client.call_tool('owner_request_review', {'plan_id': foreign, 'scope': 'arbitrary-path'})).is_error
            assert (await client.call_tool('owner_request_review', {'plan_id': foreign, 'approved': True})).is_error
            assert not list(runtime.glob('*.started'))
            assert not list(managed.glob('*.started'))
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [], audit
        assert {entry['plan_id'] for entry in audit['shown_plans']} == expected_shown
        assert len(audit['shown_plans']) == len(expected_shown)
        print('Strict graphical owner review: all service kinds and managed policy passed; '
              + ('all services enabled' if contacts else 'disabled Contacts refused'))


async def run_cleanup_setup():
    with tempfile.TemporaryDirectory(prefix='cleanup-review-stdio-') as directory:
        home = Path(directory)
        root = home / 'installation'
        local = root / 'local'
        local.mkdir(parents=True, mode=0o700)
        runtime = home / 'runtime'
        account = 'synthetic@example.com'
        settings = {'account': account, 'apple_keychain_service': 'synthetic-keychain',
                    'access_mode': 'write-enabled', 'dav_reads': False,
                    'services': {'mail': True, 'calendar': False, 'contacts': False},
                    'approval_policy': 'local-receipt', 'runtime_directory': str(runtime)}
        settings_path = local / 'settings.json'
        settings_path.write_text(json.dumps(settings))
        settings_path.chmod(0o600)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'BRIDGE_TEST_MANIFEST': str(home / 'manifest.json'),
               'ICLOUD_MAIL_ACCOUNT': account, 'ICLOUD_MAIL_ACCESS': 'write-enabled',
               'ICLOUD_APPROVAL_POLICY': 'local-receipt', 'ICLOUD_CONFIG_ROOT': str(root),
               'ICLOUD_RUNTIME_ROOT': str(runtime), 'ICLOUD_ENABLE_MAIL': '1',
               'ICLOUD_ENABLE_POLICY_MANAGEMENT': '0', 'FASTMCP_SHOW_SERVER_BANNER': 'false',
               'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/owner_review_stdio_server.py')], env=env, cwd=home)
        request = {'account': account, 'operations': ['archive', 'mark_read'],
                   'source_folders': ['Synthetic Inbox'], 'destinations': {'archive': 'Synthetic Archive'},
                   'max_batch': 2, 'max_24h': 5, 'days': 2, 'rules_reference': 'Synthetic scenario'}
        async with Client(params, mode='auto', read_timeout_seconds=20) as client:
            proposal = content(await client.call_tool('email_prepare_cleanup_grant', {'request': request}))
            identity = proposal['plan_id']
            assert proposal['review_tool'] == 'owner_request_review'
            assert (await client.call_tool('email_activate_cleanup_grant', {'plan_id': identity})).is_error
            assert json.loads(settings_path.read_text()) == settings
            content(await client.call_tool('owner_request_review', {'plan_id': identity}))
            assert await wait_review(client, identity) == 'approved'
            result = content(await client.call_tool('email_activate_cleanup_grant', {'plan_id': identity}))
            assert result['status'] == 'applied' and result['mail_changed'] is False
            grant = result['grant_id']
            status = content(await client.call_tool('email_cleanup_grant_status', {}))
            assert status['active'] and status['activation_verified'] and status['grant_id'] == grant
            assert (await client.call_tool('email_activate_cleanup_grant', {'plan_id': identity})).is_error
            assert content(await client.call_tool('email_revoke_cleanup_grant', {'grant_id': grant}))['revoked']
            assert not content(await client.call_tool('email_cleanup_grant_status', {}))['active']
        assert json.loads((home / 'audit.json').read_text())['blocked_events'] == []
        print('Standing cleanup: synthetic MCP preview, local review, activation and revocation passed')


async def main():
    await run(True)
    await run(False)
    await run_cleanup_setup()


if __name__ == '__main__':
    asyncio.run(main())
