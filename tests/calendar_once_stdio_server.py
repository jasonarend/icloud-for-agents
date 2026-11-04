"""Synthetic Calendar MCP server; block real network and credential access."""

import atexit
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

events = []
requests = {"timezone_reads": 0, "event_reads": 0, "occurrence_reads": 0, "writes": 0}


def audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "subprocess.Popen"}:
        events.append(event)
        raise RuntimeError("Synthetic Calendar server forbids network and child processes")


sys.addaudithook(audit)
atexit.register(lambda: Path(os.environ["BRIDGE_TEST_AUDIT"]).write_text(json.dumps({"blocked_events": events, "requests": requests})))

from icloud_for_agents import credentials


def no_credentials():
    events.append("credential_lookup")
    raise AssertionError("Synthetic Calendar must not access credentials")


credentials.credentials = no_credentials
from icloud_for_agents import server
from icloud_for_agents.approvals import PlanStore
from icloud_for_agents.dav import DAVReader
from icloud_for_agents.dav_changes import DAVChanges, replace_properties, unfold

COLLECTION = "https://caldav.icloud.com/synthetic/"
TIMEZONE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:America/Chicago
BEGIN:DAYLIGHT
DTSTART:20070311T020000
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:CDT
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20071104T020000
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:CST
END:STANDARD
END:VTIMEZONE
END:VCALENDAR
"""
objects = {}
server._dav_owner = SimpleNamespace(account="synthetic@example.com", _password="fake-only", store=PlanStore())


def known_collection(self, kind, url):
    if kind != "cal" or url != COLLECTION:
        raise ValueError("Unknown synthetic calendar")
    return COLLECTION


def transport(self, method, url, body, depth):
    if method == "PROPFIND" and url == COLLECTION and depth == 0 and "<cal:calendar-timezone/>" in body:
        requests["timezone_reads"] += 1
        response = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                    '<d:response><d:href>' + COLLECTION + '</d:href><d:propstat><d:prop>'
                    '<cal:calendar-timezone>' + escape(TIMEZONE) + '</cal:calendar-timezone>'
                    '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
        return 207, {}, response.encode()
    if method == "REPORT" and url == COLLECTION and depth == 1 and '<cal:calendar-multiget ' in body:
        requests["occurrence_reads"] += 1
        matches = [resource for resource in objects if '<d:href>' + urlsplit(resource).path + '</d:href>' in body]
        if len(matches) != 1 or 'start="20261116T160000Z"' not in body:
            raise ValueError("Unexpected synthetic occurrence request")
        resource = matches[0]
        stored = objects[resource]
        projection = replace_properties(unfold(stored["content"]), {
            "RRULE": [], "RECURRENCE-ID": ["RECURRENCE-ID:20261116T160000Z"],
            "DTSTART": ["DTSTART:20261116T160000Z"], "DTEND": ["DTEND:20261116T170000Z"]}, "END:VEVENT")
        response = ('<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">'
                    '<d:response><d:href>' + resource + '</d:href><d:propstat><d:prop><d:getetag>' +
                    escape(stored['etag']) + '</d:getetag><cal:calendar-data>' + escape(projection) +
                    '</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
                    '</d:propstat></d:response></d:multistatus>')
        return 207, {}, response.encode()
    if method != "GET":
        raise ValueError("Unexpected synthetic request")
    requests["event_reads"] += 1
    if url not in objects:
        return 404, {}, b""
    stored = objects[url]
    return 200, {"etag": stored["etag"]}, stored["content"].encode()


def write(self, kind, item):
    if kind != "cal" or item["operation"] not in {"create", "update", "delete", "delete_occurrence"}:
        raise ValueError("Unexpected synthetic write")
    operation, url = item["operation"], item["url"]
    if operation == "create":
        if url in objects:
            raise ValueError("Synthetic create must use an unused resource")
    elif url not in objects or objects[url]["etag"] != item["before"]["etag"]:
        raise ValueError("Synthetic conditional update failed")
    requests["writes"] += 1
    if operation == "delete":
        del objects[url]
    else:
        objects[url] = {"content": item["content"], "etag": '"synthetic-' + str(requests["writes"]) + '"'}
    return {"url": url, "operation": operation, "status": "applied"}


DAVReader.known_collection = known_collection
DAVReader._https = transport
DAVChanges._write = write
server.main()
