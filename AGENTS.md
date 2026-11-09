# Working on iCloud for Agents

Preserve existing user changes and keep private configuration, credentials, account data and local execution records out of commits.

## Version changes before pushing

Before pushing a batch that changes runtime behavior, dependencies or installation/setup code, choose and prepare its version automatically. Use a patch bump for compatible fixes, a minor bump for compatible features, and a major bump for breaking changes. Prepare one version for the complete batch rather than pushing a fix and a separate version-only follow-up.

Update `pyproject.toml`, `src/icloud_for_agents/version.py`, the README version and the dated changelog entry together. Describe the user-facing changes in the changelog. Documentation-only or test-only batches may keep the current version unless the user requests a release.

Before committing, run the relevant checks and `.venv/bin/python scripts/check-release.py`. After committing, fetch the intended remote branch and immediately before pushing run `.venv/bin/python scripts/check-release.py --pre-push`. This checks the committed version and outgoing functional changes against `origin/main`; a new functional push batch needs a newer version even when the previous version is not tagged yet. Use `--remote-ref` only when the intended remote branch differs. Commit all release-version references before this check. Include the version changes in the same push as the functional changes so a follow-up push does not repeat GitHub Actions unnecessarily.

Commit and push only when authorized. A request to commit or push does not authorize creating a tag, publishing a GitHub release or uploading a package; do those only when the user requests a release.
