# Contributing and testing

Active code is under `src/icloud_for_agents/`, reusable setup utilities under `scripts/`, synthetic tests under `tests/`, and nonsecret templates under `deployment/`. The upstream server is not vendored or imported. Provenance and its required MIT notice live under `third_party/icloud-mcp/`.

Use an isolated environment with the reviewed dependencies. From the root:

```sh
.venv/bin/python tests/run_offline.py
.venv/bin/python tests/stdio_smoke.py
.venv/bin/python tests/policy_stdio_smoke.py
.venv/bin/python scripts/check-dependency-licenses.py
```

Reports go to ignored `build/reports/`, not the root. The offline runner isolates HOME/environment and refuses network, DNS, listeners and subprocesses. The stdio smoke test separately starts a credential-free guarded child for real initialize/tools-list and input/read-only refusal checks. Neither connects to Apple or the tunnel. Do not substitute live credentials for synthetic fixtures.

After building a source distribution, check its full validation workflow with `.venv/bin/python scripts/check-sdist.py dist/icloud_for_agents-VERSION.tar.gz`. Run the three commands above again from an extracted copy using its own installed environment; successful tests in the original checkout do not prove that the archive includes the required runners and fixtures.

Run `tests/policy_stdio_smoke.py` as well to exercise the opt-in production-policy tools through real JSON-RPC with fake IMAP and an isolated home. Strict owner receipts are tested with a simulated TTY; trusted-client admission is labeled as an assertion, not an authenticated human interaction. Test reports provide the current counts and failure details.

Preserve exact accounts, folder/UIDVALIDITY and ETag identity checks, snapshot hashes, durable one-shot markers and uncertainty reconciliation. Do not retry writes blindly or treat tool annotations/model booleans as human authorization. Changes that widen access or destructive scope require separate review and owner authorization.

Keep private settings, handoffs, mail bodies, real message/plan IDs and execution evidence outside distributable files. Tests use reserved synthetic data. Generated metadata and caches must stay ignored. Check links, package contents and candidate files before [release](releasing.md).

## Client and platform compatibility contributions

Help test the [documented client routes](../getting-started/clients.md), including Grok coding CLI and Meta Muse Code, or implement missing connections for Grok Bot, Grok web and the Meta Muse consumer app. Other macOS architectures and Python versions also need validation. Linux/Windows support needs a documented secure credential provider and installation workflow as well as dependency testing; a successful package install alone does not establish support.

Open an issue or pull request with:

- Client name/version, operating system/architecture, Python version and project revision.
- Where the server runs, transport used, configuration scope and sanitized reproduction steps.
- Results of the offline and stdio checks, tool discovery, and actual read-only calls for each service tested. Mark anything untested or blocked explicitly.
- Required setup changes, relevant official documentation and sanitized errors, including any remaining authentication or client restrictions.

Use your own account for optional live verification and keep account details, credentials and private content out of reports. Test writes only under separate explicit authorization and describe their results separately. A read-only connection report is a useful contribution on its own.

For new transports or credential providers, describe authentication, local secret handling, connection lifecycle and how existing approval and account checks remain effective. Include synthetic regression coverage and setup documentation. The OpenAI private tunnel is specific to ChatGPT; other remote clients need their own verified connection path. These are contribution opportunities, not commitments that the project already supports those environments.
