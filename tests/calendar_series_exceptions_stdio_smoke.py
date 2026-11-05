"""Whole-series bounded exception content/deletion over real synthetic MCP."""
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


async def run(policy):
    with tempfile.TemporaryDirectory(prefix='calendar-series-exceptions-stdio-') as directory:
        home = Path(directory)
        env = {'HOME': str(home), 'PATH': os.defpath, 'PYTHONPATH': str(ROOT / 'src'),
               'BRIDGE_TEST_AUDIT': str(home / 'audit.json'), 'ICLOUD_MAIL_ACCOUNT': 'synthetic@example.com',
               'ICLOUD_ENABLE_MAIL': '0', 'ICLOUD_ENABLE_CALENDAR': '1', 'ICLOUD_ENABLE_CONTACTS': '0',
               'ICLOUD_MAIL_ACCESS': 'read-only', 'ICLOUD_APPROVAL_POLICY': policy, 'BRIDGE_FAKE_LOCAL_REVIEW': '1',
               'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_CHECK_FOR_UPDATES': 'off'}
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'tests/calendar_once_stdio_server.py')], env=env, cwd=home)
        async with Client(params, mode='auto', read_timeout_seconds=20) as client:
            async def change(item, deletion=False):
                args = {'calendar_url': COLLECTION, 'changes': [item]}
                if deletion and policy == 'trusted-client':
                    args['deletion_authorization'] = 'trusted-client'
                proposal = content(await client.call_tool('calendar_prepare_once', args))
                await authorize(client, proposal)
                result = content(await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']}))
                assert result['status'] == 'applied', result
                return proposal, result
            original, _ = await change({'operation': 'create', 'fields': {'summary': 'Synthetic original',
                'start': '2026-11-09T16:00:00Z', 'end': '2026-11-09T17:00:00Z', 'timezone': 'UTC',
                'recurrence': {'frequency': 'weekly', 'count': 3}}})
            url = original['review']['payload']['changes'][0]['url']
            await change({'operation': 'update', 'url': url, 'recurrence_scope': 'occurrence',
                'occurrence_start': '2026-11-16T16:00:00Z', 'fields': {'summary': 'Customized occurrence'}})
            proposal, result = await change({'operation': 'update', 'url': url, 'recurrence_scope': 'series',
                'fields': {'summary': 'Whole-series title'}})
            item = proposal['review']['payload']['changes'][0]
            assert item['series_exception_update']['component_count'] == 2
            assert item['content'].count('SUMMARY:Whole-series title') == 2
            assert 'RECURRENCE-ID:20261116T160000Z' in item['content']
            assert result['completed'][0]['verification'] == 'reviewed_whole_series_exception_content_readback'
            proposal, result = await change({'operation': 'delete', 'url': url, 'recurrence_scope': 'series',
                'irreversible_confirmed': True}, deletion=True)
            assert result['completed'][0]['verification'] == 'series_resource_absent'
            assert (await client.call_tool('dav_execute_approved', {'plan_id': proposal['plan_id']})).is_error
        audit = json.loads((home / 'audit.json').read_text())
        assert audit['blocked_events'] == [] and audit['requests']['writes'] == 4
        print(policy + ': whole-series exception content update and deletion passed MCP with full readback')


async def main():
    await run('trusted-client')
    await run('local-receipt')


if __name__ == '__main__':
    asyncio.run(main())
