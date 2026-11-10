# Distribution and release verification

## Per-user source and private connections

Source distribution lets each user install their own environment and connect their own Apple credentials and MCP client. It must never contain a working user's Keychain material, settings, tunnel profile, message data, approval records, handoffs or supplied private assets. Local runtime paths are ignored and are not release inputs.

ChatGPT private custom MCP connections are distinct from public plugins. Catalog eligibility requires a separate provider review. OpenAI's [desktop-only deployment guide](https://developers.openai.com/plugins/deploy/desktop-only) describes local stdio packaging and current partner restrictions; [plugin building](https://developers.openai.com/plugins/build/plugins) and [third-party integration guidelines](https://developers.openai.com/plugins/plugin-guidelines#third-party-content-and-integrations) add eligibility requirements. Desktop-specific packaging is not included. Do not promise a public catalog listing or compatibility with untested clients.

## Prepare the version before pushing

Prepare one version for a batch of functional changes before its first push. Compatible fixes normally receive a patch bump, compatible features a minor bump, and breaking changes a major bump. Documentation-only and test-only changes may keep the current version unless a release is requested.

Update `pyproject.toml`, `src/icloud_for_agents/version.py`, the README version and the first dated changelog entry together, then run:

```sh
.venv/bin/python scripts/check-release.py
```

The preparation check uses the latest reachable stable release tag and checks the four version references. Fetch release tags first if this is a new checkout. After committing, fetch the intended remote branch and run the committed check immediately before pushing:

```sh
.venv/bin/python scripts/check-release.py --pre-push
```

This mode checks committed `HEAD` against `origin/main`, refuses uncommitted release-version references, and requires a newer committed version for outgoing functional changes. Each new functional push batch must advance the remote version even if the previous version has not been tagged yet. Use `--remote-ref` for a different intended remote branch. Both checks are local and do not fetch or push anything. Include the version and changelog in the same push as the changes to avoid a second version-only GitHub Actions run. Coding agents follow the same requirement in [AGENTS.md](../../AGENTS.md).

Publish a release only when the owner requests one. Tag the exact source commit whose hosted checks passed; reuse that result rather than rerunning the same checks for the tag push. Check the tag's commit before publishing.

## Before a release

- Compare the commits and changes since the previous published release with the exact proposed release commit. Update [CHANGELOG.md](../../CHANGELOG.md) before tagging: include meaningful features, fixes, setup changes and other relevant improvements in clear, useful groups, and attribute each change to the release that first included it. Keep releases newest first and do not add an Unreleased placeholder.
- Use the intended local publication date in the changelog and verify it before publishing. If the date changes before publication, update the entry, repeat the checks and ensure the release tag identifies the checked commit. Commit the changelog with the release version references before running the checks for that commit.
- Prepare the GitHub release notes from the reviewed changelog entry, and check that the version, date and substantive changes agree before publication. The release script verifies version references; it cannot determine whether the changelog is complete or correctly attributes changes.
- Review the exact staged/source archive and wheel/sdist contents, Git history and dependency licenses. Ignore rules alone do not remove tracked data.
- Run guarded tests and credential-free discovery from a sanitized copy; build/install the package into a temporary environment with no private settings.
- Distinguish reused installed dependencies from a fresh network dependency bootstrap and a clean OS test. Record versions, platform and limits.
- Validate relative documentation links and all install paths after a move. Keep source/package version separate from app wrapper and tunnel-client versions.
- Build the sdist, run `.venv/bin/python scripts/check-sdist.py dist/icloud_for_agents-VERSION.tar.gz`, then extract it into a disposable directory and run the documented offline/stdio checks there. The archive must contain every test runner and fixture, not just `test_*.py`.
- GitHub private vulnerability reporting is enabled for the public repository. Before publishing a release, verify that the **Report a vulnerability** form linked from [SECURITY.md](../../SECURITY.md) is still available and that maintainers monitor incoming reports.
- Review the complete relevant security surface. A partial scan or passing privacy patterns is not an exhaustive assurance.
- Run `scripts/check-dependency-licenses.py` with the project interpreter and review its notice evidence. Preserve the distinction between inventory verification, advisory scans, an independent security audit and legal license review; do not call a release certified on the basis of automated checks.
- Review the [upgrade, rollback and retained-data checklist](../getting-started/maintenance.md). The GitHub macOS workflow runs synthetic checks, license inventory verification and package checks; a workflow file alone is not a successful hosted CI run.
- Obtain explicit approval before a remote, commit, push, package upload or publication.
