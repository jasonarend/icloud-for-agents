"""Exercise this-and-later deletion through MCP with synthetic Calendar data."""
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


async def main():
    with tempfile.TemporaryDirectory(prefix='calendar-future-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '0',
               'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': 'trusted-client',
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/calendar_once_stdio_server.py')], env=env, cwd=home)
        async with Client(params, mode='auto', read_timeout_seconds=20) as client:
            fields = {'summary': 'Synthetic', 'start': '2026-11-09T16:00:00Z',
                      'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                      'recurrence': {'frequency': 'weekly', 'count': 3}}
            proposal = content(await client.call_tool('calendar_prepare_once',
                {'calendar_url': COLLECTION, 'changes': [{'operation': 'create', 'fields': fields}]}))
            item = proposal['review']['payload']['changes'][0]
            assert content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))['status'] == 'applied'
            change = {'operation': 'delete_future_occurrences', 'url': item['url'], 'recurrence_scope': 'future',
                      'occurrence_start': '2026-11-16T16:00:00Z', 'irreversible_confirmed': True}
            proposal = content(await client.call_tool('calendar_prepare_once',
                {'calendar_url': COLLECTION, 'changes': [change], 'deletion_authorization': 'trusted-client'}))
            reviewed = proposal['review']['payload']['changes'][0]
            assert not proposal['local_receipt_required']
            assert 'UNTIL=20261116T155959Z' in reviewed['content'] and 'COUNT=3' not in reviewed['content']
            assert reviewed['occurrence_proof']['recurrence_id'] == '20261116T160000Z'
            result = content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))
            assert result['status'] == 'applied' and result['completed'][0]['verification'] == 'reviewed_recurring_event_readback'
            assert (await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']})).is_error
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [] and audit['requests']['writes'] == 2
        print('Future deletion passed over MCP with provider identity proof and readback; synthetic data only')


if __name__ == '__main__':
    asyncio.run(main())
