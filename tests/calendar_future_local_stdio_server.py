"""Synthetic local-zone Calendar provider; no network, secrets or GUI access."""
import atexit
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from xml.sax.saxutils import escape

blocked = []
requests = {'writes': 0, 'occurrence_reads': 0}
objects = {}


def audit(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'socket.getaddrinfo', 'subprocess.Popen'}:
        blocked.append(event)
        raise RuntimeError('Synthetic local Calendar forbids external access')


sys.addaudithook(audit)
atexit.register(lambda: Path(os.environ['BRIDGE_TEST_AUDIT']).write_text(json.dumps({'blocked_events': blocked, 'requests': requests})))
from icloud_for_agents import credentials, owner_dialog


def no_credentials():
    blocked.append('credential_lookup')
    raise AssertionError('Synthetic Calendar must not read credentials')


credentials.credentials = no_credentials
owner_dialog.request_owner_decision = lambda review, heading: 'approved'
from icloud_for_agents import server
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.calendar_occurrence_changes import _resource, _line, occurrence_source
from icloud_for_agents.calendar_recurrence import utc_datetime
from icloud_for_agents.core import SafetyError
from icloud_for_agents.dav import DAVReader, NS, xml_document
from icloud_for_agents.dav_changes import DAVChanges
from test_calendar_timezone import CHICAGO

COLLECTION = 'https://caldav.icloud.com/synthetic/'
server._dav_owner = SimpleNamespace(account='synthetic@example.com', _password='fake-only', store=PlanStore())


def known_collection(self, kind, url):
    assert kind == 'cal' and url == COLLECTION
    return COLLECTION


def transport(self, method, url, body, depth):
    if method == 'PROPFIND':
        assert url == COLLECTION and depth == 0 and '<cal:calendar-timezone/>' in body
        return 207, {}, ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            '<d:response><d:href>' + COLLECTION + '</d:href><d:propstat><d:prop><cal:calendar-timezone>' +
            escape(CHICAGO) + '</cal:calendar-timezone></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
            '</d:propstat></d:response></d:multistatus>').encode()
    if method == 'REPORT':
        assert url == COLLECTION and depth == 1
        requested = xml_document(body.encode()).find('.//cal:expand', NS)
        from datetime import datetime, timezone
        target = datetime.strptime(requested.attrib['start'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        requests['occurrence_reads'] += 1
        rows = []
        for resource, stored in objects.items():
            try:
                master, current, _, values = occurrence_source(stored['content'], target.isoformat())
            except SafetyError:
                continue
            # An intentional Apple-shaped projection: local DTSTART/DTEND,
            # UTC RID, full matching expansion metadata, no VTIMEZONE.
            projection = ['BEGIN:VCALENDAR', 'X-EXPANDED:True',
                'X-MASTER-DTSTART:' + master['values']['DTSTART'].split(':', 1)[1],
                'X-MASTER-RRULE:' + master['values']['RRULE'].split(':', 1)[1].replace(';', '\\;').replace(',', '\\,'),
                'BEGIN:VEVENT', 'DTSTAMP:20261101T000000Z']
            projection.extend(name + ':' + value for name, value in values.items()
                if name not in {'RRULE', 'DTSTART', 'DTEND', 'RECURRENCE-ID', 'DTSTAMP'})
            projection.extend([_line('DTSTART', current['start'], current['zone']),
                _line('DTEND', current['end'], current['zone']),
                'RECURRENCE-ID:' + target.strftime('%Y%m%dT%H%M%SZ'), 'END:VEVENT', 'END:VCALENDAR', ''])
            rows.append('<d:response><d:href>' + resource + '</d:href><d:propstat><d:prop><d:getetag>' +
                escape(stored['etag']) + '</d:getetag><cal:calendar-data>' + escape('\r\n'.join(projection)) +
                '</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
        return 207, {}, ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">' +
                         ''.join(rows) + '</d:multistatus>').encode()
    assert method == 'GET'
    if url not in objects:
        return 404, {}, b''
    stored = objects[url]
    return 200, {'etag': stored['etag']}, stored['content'].encode()


def write(self, kind, item):
    assert kind == 'cal'
    operation, url = item['operation'], item['url']
    if operation == 'create':
        assert url not in objects
    else:
        assert objects[url]['etag'] == item['before']['etag']
    requests['writes'] += 1
    if operation == 'delete':
        del objects[url]
    else:
        objects[url] = {'content': item['content'], 'etag': '"synthetic-' + str(requests['writes']) + '"'}
    return {'url': url, 'operation': operation, 'status': 'applied'}


DAVReader.known_collection = known_collection
DAVReader._https = transport
DAVChanges._write = write
server.main()
