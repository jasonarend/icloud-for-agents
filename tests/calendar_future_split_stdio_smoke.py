"""Future content split through real MCP with synthetic provider and owner review."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'https://caldav.icloud.com/synthetic/'


def content(result):
    assert not result.is_error, result
    return result.structured_content or json.loads(result.content[0].text)


async def authorize(client, proposal):
    if not proposal['local_receipt_required']:
        return
    content(await client.call_tool('owner_request_review', {'plan_id': proposal['plan_id']}))
    for _ in range(200):
        status = content(await client.call_tool('owner_review_status', {'plan_id': proposal['plan_id']}))['status']
        if status == 'approved':
            return
        assert status == 'pending', status
        await asyncio.sleep(0.01)
    raise AssertionError('Synthetic graphical approval did not finish')


async def run(policy, change_time=False):
    with tempfile.TemporaryDirectory(prefix='calendar-future-split-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '0',
               'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': policy, 'BRIDGE_FAKE_LOCAL_REVIEW': '1',
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/calendar_once_stdio_server.py')], env=env, cwd=home)
        async with Client(params, mode='auto', read_timeout_seconds=20) as client:
            fields = {'summary': 'Synthetic original', 'description': 'Retain original history',
                      'start': '2026-11-09T16:00:00Z', 'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                      'recurrence': {'frequency': 'weekly', 'count': 3}}
            original = content(await client.call_tool('calendar_prepare_once',
                {'calendar_url': COLLECTION, 'changes': [{'operation': 'create', 'fields': fields}]}))
            await authorize(client, original)
            assert content(await client.call_tool('dav_execute_approved', {'plan_id': original['plan_id']}))['status'] == 'applied'
            url = original['review']['payload']['changes'][0]['url']
            future_fields = {'summary': 'Synthetic future'}
            if change_time:
                future_fields.update(start='2026-11-16T18:00:00Z', end='2026-11-16T19:30:00Z', timezone='UTC')
            change = {'operation': 'update', 'url': url, 'recurrence_scope': 'future',
                      'occurrence_start': '2026-11-16T16:00:00Z', 'fields': future_fields}
            proposal = content(await client.call_tool('calendar_prepare_once', {'calendar_url': COLLECTION, 'changes': [change]}))
            item = proposal['review']['payload']['changes'][0]
            split = item['future_split']
            assert split['remaining_count'] == 2 and split['preceding_occurrences'] == 1
            assert split['new_resource']['url'] != url
            assert 'UNTIL=20261116T155959Z' in item['content'] and 'SUMMARY:Synthetic original' in item['content']
            assert 'COUNT=2' in split['new_resource']['content'] and 'SUMMARY:Synthetic future' in split['new_resource']['content']
            if change_time:
                assert split['time_change']['new_start'] == '2026-11-16T18:00:00+00:00'
                assert 'DTSTART:20261116T180000Z' in split['new_resource']['content']
                assert 'DTEND:20261116T193000Z' in split['new_resource']['content']
                assert 'DTSTART:20261109T160000Z' in item['content']
            else:
                assert split['time_change'] is None
            if policy == 'local-receipt':
                assert (await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']})).is_error
            await authorize(client, proposal)
            result = content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))
            assert result['status'] == 'applied'
            assert [entry['operation'] for entry in result['completed']] == ['create', 'update']
            assert result['completed'][0]['url'] == split['new_resource']['url']
            assert result['completed'][1]['url'] == url
            assert (await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']})).is_error
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [] and audit['requests']['writes'] == 3
        assert audit['requests']['occurrence_reads'] == 3
        print(policy + (' time change' if change_time else ' content change') + ': future split passed MCP approval, provider proof, new-resource readback, history truncation and replay refusal')


async def main():
    await run('trusted-client')
    await run('local-receipt')
    await run('trusted-client', True)
    await run('local-receipt', True)


if __name__ == '__main__':
    asyncio.run(main())
