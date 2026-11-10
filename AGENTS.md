# Working on iCloud for Agents

Preserve existing user changes and keep private configuration, credentials, account data and local execution records out of commits.

## Version changes before pushing

Before pushing a batch that changes runtime behavior, dependencies or installation/setup code, choose and prepare its version automatically. Use a patch bump for compatible fixes, a minor bump for compatible features, and a major bump for breaking changes. Prepare one version for the complete batch rather than pushing a fix and a separate version-only follow-up.

Update `pyproject.toml`, `src/icloud_for_agents/version.py` and the README version together before a functional push. Documentation-only or test-only batches may keep the current version unless the user requests a release. Update the changelog when preparing a release, not for ordinary commits or pushes.

Before committing local work, run the relevant checks and `.venv/bin/python scripts/check-release.py --local-commit`. This checks version consistency without creating a release entry for work in progress. Before pushing, prepare the complete batch's version and run `.venv/bin/python scripts/check-release.py`; commit the version references, fetch the intended remote branch and immediately before pushing run `.venv/bin/python scripts/check-release.py --pre-push`. This checks the committed version and outgoing functional changes against `origin/main`; a new functional push batch needs a newer version even when the previous version is not tagged yet. Use `--remote-ref` only when the intended remote branch differs. Include the version changes in the same push as the functional changes so a follow-up push does not repeat GitHub Actions unnecessarily.

Commit and push only when authorized. A request to commit or push does not authorize creating a tag, publishing a GitHub release or uploading a package; do those only when the user requests a release.

## Changelog before every release

Before creating a release or its tag, review the changes from the previous published release through the exact proposed release commit. Update `CHANGELOG.md` for that version, including meaningful user-facing features, fixes, setup changes and other relevant improvements. Attribute changes to the release that first included them, keep releases newest first, and use clear language grouped in a useful order. Use the intended local publication date and verify it before publication. If the date changes before publication, update the entry, repeat the checks and ensure the release tag identifies the checked commit. Do not add an Unreleased placeholder.

Run `.venv/bin/python scripts/check-release.py --release` and the other release checks, and ensure the updated changelog is committed in the exact source commit whose hosted checks passed before tagging. Base the GitHub release notes on the reviewed changelog entry and check that their version, date and substantive changes agree. The automated version check cannot establish that a changelog is complete or accurately attributed; perform this review for every release, including patch and documentation-only releases.
