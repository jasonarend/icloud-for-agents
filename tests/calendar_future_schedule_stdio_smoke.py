"""Complete future schedule replacement over real MCP with synthetic providers."""
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


async def run(policy, local):
    with tempfile.TemporaryDirectory(prefix='calendar-future-schedule-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '0',
               'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': policy,
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/calendar_future_local_stdio_server.py')], env=env, cwd=home)
        async with Client(params, mode='auto', read_timeout_seconds=30) as client:
            initial = content(await client.call_tool('calendar_prepare_once', {'calendar_url': COLLECTION,
                'changes': [{'operation': 'create', 'fields': {'summary': 'Synthetic original schedule',
                    'start': '2026-10-26T09:00:00-05:00' if local else '2026-10-26T14:00:00Z',
                    'end': '2026-10-26T10:00:00-05:00' if local else '2026-10-26T15:00:00Z',
                    'timezone': 'America/Chicago' if local else 'UTC',
                    'recurrence': {'frequency': 'weekly', 'count': 6}}}]}))
            await authorize(client, initial)
            assert content(await client.call_tool('dav_execute_approved', {'plan_id': initial['plan_id']}))['status'] == 'applied'
            old = initial['review']['payload']['changes'][0]
            fields = {'start': '2026-11-12T11:00:00-06:00' if local else '2026-11-12T17:00:00Z',
                      'end': '2026-11-12T12:00:00-06:00' if local else '2026-11-12T18:00:00Z',
                      'timezone': 'America/Chicago' if local else 'UTC',
                      'recurrence': {'frequency': 'monthly', 'month_days': [12], 'count': 7}}
            proposal = content(await client.call_tool('calendar_prepare_once', {'calendar_url': COLLECTION,
                'changes': [{'operation': 'update', 'url': old['url'], 'recurrence_scope': 'future',
                    'occurrence_start': '2026-11-09T15:00:00Z' if local else '2026-11-09T14:00:00Z', 'fields': fields}]}))
            item = proposal['review']['payload']['changes'][0]
            split = item['future_split']
            assert split['schedule_replacement']['replaced_remaining_count'] == 4
            assert split['remaining_count'] == 7
            assert split['schedule_replacement']['recurrence'] == fields['recurrence']
            assert 'COUNT=7;BYMONTHDAY=12' in split['new_resource']['content']
            assert ('DTSTART;TZID=America/Chicago:20261112T110000' if local else 'DTSTART:20261112T170000Z') in split['new_resource']['content']
            assert ('DTSTART;TZID=America/Chicago:20261026T090000' if local else 'DTSTART:20261026T140000Z') in item['content']
            assert ('UNTIL=20261109T145959Z' if local else 'UNTIL=20261109T135959Z') in item['content']
            assert not any('including past occurrences' in effect for effect in item['recurrence_effects'])
            assert 'timezone_binding' not in item
            await authorize(client, proposal)
            result = content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))
            assert result['status'] == 'applied', result
            assert [entry['operation'] for entry in result['completed']] == ['create', 'update']
            assert (await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']})).is_error
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [] and audit['requests']['writes'] == 3
        print(policy + (' local' if local else ' UTC') + ': complete future date/rule/end-condition replacement passed MCP with unchanged history')


async def main():
    for policy in ('trusted-client', 'local-receipt'):
        for local in (False, True):
            await run(policy, local)


if __name__ == '__main__':
    asyncio.run(main())
