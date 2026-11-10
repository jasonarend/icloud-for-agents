# Contributing and testing

Active code is under `src/icloud_for_agents/`, reusable setup utilities under `scripts/`, synthetic tests under `tests/`, and nonsecret templates under `deployment/`. The upstream server is not vendored or imported. Provenance and required notices live under `third_party/icloud-mcp/`, `third_party/remctl/` and root `NOTICE.md`.

See [icons and client compatibility](branding.md) for the packaged MCP icon,
directory/client exports, asset regeneration and the limits of client-side rendering.
See [project metadata and ChatGPT refresh](metadata.md) for homepage/source links,
version and description checks, and separately stored plugin-listing information.

Use an isolated environment with the reviewed dependencies. From the root:

```sh
.venv/bin/python tests/run_offline.py
.venv/bin/python tests/stdio_smoke.py
.venv/bin/python tests/modern_stdio_smoke.py
.venv/bin/python tests/http_transport_smoke.py
.venv/bin/python tests/native_http_keychain_smoke.py
.venv/bin/python tests/operation_lock_process_smoke.py
.venv/bin/python tests/dual_transport_smoke.py
.venv/bin/python tests/policy_stdio_smoke.py
.venv/bin/python tests/native_stdio_smoke.py
.venv/bin/python tests/native_script_contract.py
.venv/bin/python tests/native_write_contract.py
.venv/bin/python tests/native_reminder_metadata_contract.py
.venv/bin/python tests/native_reminder_dates_contract.py
.venv/bin/python tests/native_service_isolation_contract.py
.venv/bin/python scripts/check-dependency-licenses.py
```

If native contract tests report a compiler/SDK mismatch, select the matching SDK for the test process using the currently selected Apple developer installation. The source-check workflow does this without changing the Mac's system-wide developer-tool selection.

Reports go to ignored `build/reports/`, not the root. The offline runner isolates HOME/environment and refuses network, DNS, listeners and subprocesses. The stdio smoke test separately starts a credential-free guarded child for real initialize/tools-list and input/read-only refusal checks. Neither connects to Apple or the tunnel. The modern stdio check additionally verifies current MCP negotiation, selected-service tool schemas, annotation boundaries and tool-error handling through the installed MCP client. The separate native-script contract runner executes the actual JXA read logic against synthetic application objects without Apple events; it requires macOS and does not establish native-app acceptance. Do not substitute live credentials for synthetic fixtures.

The HTTP smoke test uses synthetic client credentials and a loopback listener; the dual-transport and operation-lock checks use temporary runtimes and synthetic operations across real subprocesses. These checks do not access Keychain, Apple accounts, a tunnel or any live client. The guarded offline suite separately exercises key lifecycle behavior with an in-memory Keychain substitute. The native HTTP Keychain smoke test uses a disposable standalone Keychain and synthetic credentials, verifies that the default and search list stay unchanged, and deletes the temporary Keychain. See the [HTTP setup guide](../getting-started/http.md) for the transport's security and coordination boundaries.

After building a source distribution, check its full validation workflow with `.venv/bin/python scripts/check-sdist.py dist/icloud_for_agents-VERSION.tar.gz`. Run the validation commands above again from an extracted copy using its own installed environment. The full current source-check list lives in `.github/workflows/checks.yml`; successful tests in the original checkout do not prove that the archive includes the required runners and fixtures.

Run `tests/policy_stdio_smoke.py` as well to exercise the opt-in production-policy tools through real JSON-RPC with fake IMAP and an isolated home. `tests/owner_review_stdio_smoke.py` exercises the local-review workflow with a simulated window; command-line receipt tests use a simulated TTY. Trusted-client admission is labeled as an assertion, not an authenticated human interaction. Test reports provide the current counts and failure details.

Preserve exact accounts, folder/UIDVALIDITY and ETag identity checks, snapshot hashes, durable one-shot markers and uncertainty reconciliation. Do not retry writes blindly or treat tool annotations/model booleans as human authorization. Changes that widen access or destructive scope require separate review and owner authorization.

Keep private settings, handoffs, mail bodies, real message/plan IDs and execution evidence outside distributable files. Tests use reserved synthetic data. Generated metadata and caches must stay ignored. Check links, package contents and candidate files before [release](releasing.md).

## Client and platform compatibility contributions

Help test the [documented client routes](../getting-started/clients.md), including Grok Bot's custom MCP option, Grok coding CLI and Meta Muse Code, or propose secure remote connections for Grok web and the Meta Muse consumer app. Grok Bot has not been tested end to end because the maintainer did not have a Grok subscription; confirming its MCP execution location and service access would be especially useful. [Open an issue](https://github.com/jasonarend/icloud-for-agents/issues) with problems or successful setup results. Other macOS architectures and Python versions also need validation. Linux/Windows support needs a documented secure credential provider and installation workflow as well as dependency testing; a successful package install alone does not establish support.

Open an issue or pull request with:

- Client name/version, operating system/architecture, Python version and project revision.
- Where the server runs, transport used, configuration scope and sanitized reproduction steps.
- Results of the offline and stdio checks, tool discovery, and actual read-only calls for each service tested. Mark anything untested or blocked explicitly.
- Required setup changes, relevant official documentation and sanitized errors, including any remaining authentication or client restrictions.

Use your own account for optional live verification and keep account details, credentials and private content out of reports. Test writes only under separate explicit authorization and describe their results separately. A read-only connection report is a useful contribution on its own.

For new transports or credential providers, describe authentication, local secret handling, connection lifecycle and how existing approval and account checks remain effective. Include synthetic regression coverage and setup documentation. The OpenAI private tunnel is specific to ChatGPT; other remote clients need their own verified connection path. These are contribution opportunities, not commitments that the project already supports those environments.

The Reminders metadata contract compiles the shipped serializers against in-memory EventKit rules and alarms. It never opens an event store, requests OS access or changes reminder data.

The date contract runs the shipped filtering logic on in-memory Foundation components, including timezone/DST boundaries, inclusive ranges, priorities, completion and undated items. It does not read a native account.

The service-isolation contract instruments the helper's Reminders authorization-status query and verifies that Notes status and unknown-service rejection never call it. It probes Notes OS permission status only, without reading native content or opening an event store.
