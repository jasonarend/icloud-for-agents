"""Named-local future splits and deletion use real MCP and synthetic Apple shapes."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from mcp import Client, StdioServerParameters
from calendar_future_split_stdio_smoke import authorize, content

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'https://caldav.icloud.com/synthetic/'


async def run(policy, change_time=False):
    with tempfile.TemporaryDirectory(prefix='calendar-future-local-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '0',
               'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': policy,
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/calendar_future_local_stdio_server.py')], env=env, cwd=home)
        async with Client(params, mode='auto', read_timeout_seconds=30) as client:
            async def change(item, deletion=False):
                args = {'calendar_url': COLLECTION, 'changes': [item]}
                if deletion and policy == 'trusted-client':
                    args['deletion_authorization'] = 'trusted-client'
                proposal = content(await client.call_tool('calendar_prepare_once', args))
                await authorize(client, proposal)
                result = content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))
                assert result['status'] == 'applied', result
                return proposal, result
            initial, _ = await change({'operation': 'create', 'fields': {'summary': 'Synthetic local',
                'start': '2026-10-26T09:00:00-05:00', 'end': '2026-10-26T10:00:00-05:00',
                'timezone': 'America/Chicago', 'recurrence': {'frequency': 'weekly', 'count': 6}}})
            original_url = initial['review']['payload']['changes'][0]['url']
            fields = {'summary': 'Future local title'}
            if change_time:
                fields.update(start='2026-11-09T11:00:00-06:00', end='2026-11-09T12:00:00-06:00', timezone='America/Chicago')
            proposal, result = await change({'operation': 'update', 'url': original_url, 'recurrence_scope': 'future',
                'occurrence_start': '2026-11-09T15:00:00Z', 'fields': fields})
            item = proposal['review']['payload']['changes'][0]
            split = item['future_split']
            assert split['timezone'] == 'America/Chicago' and split['timezone_validation'] is not None
            assert split['preceding_occurrences'] == 2 and split['remaining_count'] == 4
            assert 'DTSTART;TZID=America/Chicago:20261026T090000' in item['content']
            assert 'UNTIL=20261109T145959Z' in item['content']
            new = split['new_resource']
            assert ('DTSTART;TZID=America/Chicago:20261109T' + ('110000' if change_time else '090000')) in new['content']
            assert new['content'].count('BEGIN:VTIMEZONE') == 1
            assert [entry['operation'] for entry in result['completed']] == ['create', 'update']
            deleted, result = await change({'operation': 'delete_future_occurrences', 'url': new['url'],
                'recurrence_scope': 'future', 'occurrence_start': '2026-11-16T' + ('17' if change_time else '15') + ':00:00Z',
                'irreversible_confirmed': True}, deletion=True)
            truncated = deleted['review']['payload']['changes'][0]['content']
            assert ('UNTIL=20261116T' + ('16' if change_time else '14') + '5959Z') in truncated
            assert 'DTSTART;TZID=America/Chicago:20261109T' in truncated
            assert (await client.call_tool('dav_execute_approved', {'plan_id': deleted['plan_id']})).is_error
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [] and audit['requests']['writes'] == 4
        print(policy + (' local time change' if change_time else ' local content change') + ': future split/deletion and full readback passed; synthetic Apple-shaped expansion only')


async def main():
    for policy in ('trusted-client', 'local-receipt'):
        for changed in (False, True):
            await run(policy, changed)


if __name__ == '__main__':
    asyncio.run(main())
