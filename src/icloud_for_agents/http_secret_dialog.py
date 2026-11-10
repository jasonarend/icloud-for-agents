"""Show an HTTP client key locally without returning it to the assistant."""

import json
import os
import subprocess
import sys

from .core import SafetyError


_SCRIPT = r"""
ObjC.import('Foundation');
ObjC.import('AppKit');
function run() {
  const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  if (Number(data.length) > 4096) throw new Error('invalid_request');
  const request = JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding)));
  if (Object.keys(request).sort().join(',') !== 'client,key,protocol_version' || request.protocol_version !== 1 ||
      typeof request.client !== 'string' || typeof request.key !== 'string' ||
      !/^ika_[0-9a-f]{32}\.[A-Za-z0-9_-]{43}$/.test(request.key)) throw new Error('invalid_request');
  const app = $.NSApplication.sharedApplication;
  // A regular app is visible in the Dock and app switcher. An accessory-only
  // osascript can leave its modal panel behind the requesting client's window.
  if (!app.setActivationPolicy($.NSApplicationActivationPolicyRegular)) throw new Error('display_unavailable');
  const menu = $.NSMenu.alloc.initWithTitle($('iCloud for Agents'));
  const applicationItem = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent($('iCloud for Agents'), null, $(''));
  const applicationMenu = $.NSMenu.alloc.initWithTitle($('iCloud for Agents'));
  applicationMenu.addItem($.NSMenuItem.alloc.initWithTitleActionKeyEquivalent($('Quit'), $('terminate:'), $('q')));
  applicationItem.submenu = applicationMenu;
  menu.addItem(applicationItem);
  const editItem = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent($('Edit'), null, $(''));
  const editMenu = $.NSMenu.alloc.initWithTitle($('Edit'));
  editMenu.addItem($.NSMenuItem.alloc.initWithTitleActionKeyEquivalent($('Copy'), $('copy:'), $('c')));
  editMenu.addItem($.NSMenuItem.alloc.initWithTitleActionKeyEquivalent($('Select All'), $('selectAll:'), $('a')));
  editItem.submenu = editMenu;
  menu.addItem(editItem);
  app.mainMenu = menu;
  app.finishLaunching;
  const alert = $.NSAlert.alloc.init;
  alert.messageText = $('Connect ' + request.client);
  alert.informativeText = $('The key below is selected. Press Command-C to copy it, then paste it only into the client’s secure credential field. Do not paste it into chat or a normal configuration file. This tool does not return the key to the assistant. Close this window after transferring it.');
  alert.alertStyle = $.NSAlertStyleInformational;
  const field = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 0, 720, 48));
  field.stringValue = $(request.key);
  field.editable = false;
  field.selectable = true;
  field.font = $.NSFont.userFixedPitchFontOfSize(13);
  alert.accessoryView = field;
  alert.addButtonWithTitle($('Done'));
  alert.layout;
  const window = alert.window;
  window.title = $('iCloud for Agents');
  window.collectionBehavior = $.NSWindowCollectionBehaviorMoveToActiveSpace | $.NSWindowCollectionBehaviorFullScreenAuxiliary;
  window.level = $.NSFloatingWindowLevel;
  window.hidesOnDeactivate = false;
  window.movableByWindowBackground = true;
  window.initialFirstResponder = field;
  window.center;
  window.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);
  window.orderFrontRegardless;
  window.makeFirstResponder(field);
  field.selectText(null);
  if (Number(alert.runModal) !== 1000) throw new Error('display_closed');
  return 'CLOSED';
}
"""


def show_client_key(client: str, token: bytes) -> str:
    """Display the key in a fixed owner-operated window; return no secret."""
    from .http_keys import BEARER
    if (not isinstance(client, str) or not 1 <= len(client) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in client)
            or not isinstance(token, bytes) or not BEARER.fullmatch(token)):
        raise SafetyError("Invalid client key display request")
    if sys.platform != "darwin":
        raise SafetyError("Client key display requires the signed-in macOS desktop")
    request = json.dumps({"protocol_version": 1, "client": client, "key": token.decode("ascii")},
                         ensure_ascii=True, separators=(",", ":"))
    environment = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    try:
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _SCRIPT],
                                input=request, text=True, encoding="utf-8", capture_output=True,
                                check=False, close_fds=True, timeout=600, env=environment)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SafetyError("The local key window did not complete; inspect client status before another action") from None
    if result.returncode or result.stdout not in ("CLOSED", "CLOSED\n"):
        raise SafetyError("The local key window did not complete; inspect client status before another action")
    return "window_closed"
