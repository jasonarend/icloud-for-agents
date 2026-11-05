"""Fixed local graphical review using the macOS components already installed.

This module returns a local dialog decision. It does not write approval receipts,
authenticate an MCP client, execute a proposal, or isolate another same-user process.
"""

import json
import os
import subprocess
import sys

from .core import SafetyError


MAX_REVIEW_CHARACTERS = 1_000_000
MAX_REQUEST_BYTES = 2_000_000
REVIEW_TIMEOUT_SECONDS = 600

# This script is fixed application code. Private proposal content enters only
# through stdin and becomes read-only text; it is never evaluated as JavaScript.
_REVIEW_SCRIPT = r"""
ObjC.import('Foundation');
ObjC.import('AppKit');

function run() {
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    if (Number(data.length) > 2000000) throw new Error('review_request_refused');
    const source = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
    if (!source) throw new Error('review_request_refused');
    const request = JSON.parse(ObjC.unwrap(source));
    const keys = Object.keys(request).sort().join(',');
    if (keys !== 'heading,protocol_version,review' || request.protocol_version !== 1 ||
        typeof request.heading !== 'string' || typeof request.review !== 'string' ||
        !request.heading.length || !request.review.length) throw new Error('review_request_refused');

    const app = $.NSApplication.sharedApplication;
    app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
    const alert = $.NSAlert.alloc.init;
    alert.messageText = $(request.heading);
    alert.informativeText = $('Review the complete proposal below. Approve applies only to this exact proposal. Cancel records no approval.');
    alert.alertStyle = $.NSAlertStyleWarning;

    const scroll = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, 720, 420));
    scroll.hasVerticalScroller = true;
    scroll.hasHorizontalScroller = false;
    scroll.borderType = $.NSBezelBorder;
    const text = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 700, 420));
    text.editable = false;
    text.selectable = true;
    text.richText = false;
    text.importsGraphics = false;
    text.verticallyResizable = true;
    text.horizontallyResizable = false;
    text.minSize = $.NSMakeSize(0, 420);
    text.maxSize = $.NSMakeSize(10000000, 10000000);
    text.autoresizingMask = $.NSViewWidthSizable;
    text.font = $.NSFont.userFixedPitchFontOfSize(11);
    text.textContainer.containerSize = $.NSMakeSize(700, 10000000);
    text.textContainer.widthTracksTextView = true;
    text.string = $(request.review);
    text.setSelectedRange($.NSMakeRange(0, 0));
    text.scrollRangeToVisible($.NSMakeRange(0, 0));
    scroll.documentView = text;
    alert.accessoryView = scroll;

    const cancel = alert.addButtonWithTitle($('Cancel'));
    const approve = alert.addButtonWithTitle($('Approve'));
    cancel.keyEquivalent = $('\r');
    cancel.keyEquivalentModifierMask = 0;
    approve.keyEquivalent = $('');
    alert.window.title = $('iCloud for Agents');
    alert.window.initialFirstResponder = cancel;
    app.activateIgnoringOtherApps(true);
    const choice = Number(alert.runModal);
    if (choice === 1001) return 'APPROVED';
    if (choice === 1000) return 'DECLINED';
    throw new Error('review_result_refused');
}
"""


def _display_text(value, *, multiline):
    """Render controls and Unicode as visible escapes without dropping content."""
    result = []
    for character in value:
        if character == "\n" and multiline:
            result.append(character)
        elif 32 <= ord(character) <= 126:
            result.append(character)
        else:
            result.append(json.dumps(character, ensure_ascii=True)[1:-1])
    return "".join(result)


def request_owner_decision(review_text: str, heading: str) -> str:
    """Return ``approved`` or ``declined`` from the fixed local owner dialog.

    The caller must independently bind this decision to the unchanged proposal,
    account, expiry and permitted state before recording any receipt. A timeout,
    unavailable GUI, process failure or unexpected response records no decision.
    """
    if (not isinstance(review_text, str) or not 1 <= len(review_text) <= MAX_REVIEW_CHARACTERS
            or not isinstance(heading, str) or not 1 <= len(heading) <= 160):
        raise SafetyError("Prepare a smaller complete proposal for graphical owner review")
    request = json.dumps({"protocol_version": 1,
                          "heading": _display_text(heading, multiline=False),
                          "review": _display_text(review_text, multiline=True)},
                         ensure_ascii=True, separators=(",", ":"))
    if len(request.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise SafetyError("Prepare a smaller complete proposal for graphical owner review")
    if sys.platform != "darwin":
        raise SafetyError("Graphical owner review requires the signed-in macOS desktop")
    environment = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    try:
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _REVIEW_SCRIPT],
                                input=request, text=True, encoding="utf-8", capture_output=True,
                                check=False, close_fds=True, timeout=REVIEW_TIMEOUT_SECONDS,
                                env=environment)
    except subprocess.TimeoutExpired:
        raise SafetyError("Owner review timed out; no approval was recorded") from None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SafetyError("The local owner review window could not complete; no approval was recorded") from None
    if result.returncode != 0:
        raise SafetyError("The local owner review window could not complete; no approval was recorded")
    if result.stdout in ("APPROVED", "APPROVED\n"):
        return "approved"
    if result.stdout in ("DECLINED", "DECLINED\n"):
        return "declined"
    raise SafetyError("The local owner review returned an invalid response; no approval was recorded")
