# Preparing a release

This guide is for maintainers publishing iCloud for Agents. For a personal installation, use [installation](../getting-started/installation.md) and [upgrade or rollback](../getting-started/maintenance.md).

## Version a push batch

Local progress commits may keep the current version. Run the relevant checks and `.venv/bin/python scripts/check-release.py --local-commit` before committing.

Before pushing runtime, dependency or setup changes, choose one version for the complete batch: a patch for compatible fixes, a minor version for compatible features, or a major version for breaking changes. Documentation-only and test-only batches may keep the existing version unless preparing a release.

Update `pyproject.toml`, `src/icloud_for_agents/version.py` and the README version together, then run:

```sh
.venv/bin/python scripts/check-release.py
```

The check uses local release tags, so fetch the release tags when preparing from a new or stale checkout. Commit the version with the functional changes. Fetch the intended remote branch and immediately before pushing run:

```sh
.venv/bin/python scripts/check-release.py --pre-push
```

The pre-push check compares committed `HEAD` with `origin/main`, requires a newer version for outgoing functional changes, and refuses uncommitted version references. Use `--remote-ref` when pushing to a different branch. Neither check fetches or pushes. Each functional push batch needs a newer version even if the previous version has not been tagged.

A push does not create a release. Update the changelog when preparing a release; do not add an Unreleased placeholder. Coding agents follow the same rules in [AGENTS.md](../../AGENTS.md).

## Review the release contents

Compare the previous published release with the exact proposed source commit. Write a dated entry in [CHANGELOG.md](../../CHANGELOG.md) describing meaningful user-facing features, fixes and setup changes. Attribute each change to the release that first included it and keep releases newest first.

Use the intended local publication date. If publication moves to another date, update the entry and repeat the checks before tagging. Commit the changelog in the source commit that will be checked and released.

Review tracked files, Git history and distribution contents for private data. A source distribution must exclude working settings, credentials, tunnel profiles, account content, approval records and local test evidence. Ignore rules do not remove previously tracked data. Preserve notices and review dependency changes using [the licensing guide](licensing.md).

## Validate the candidate

Run:

```sh
.venv/bin/python scripts/check-release.py --release
```

Then run the applicable checks in [development](development.md) and the complete [source-check workflow](../../.github/workflows/checks.yml). Check the actual results for the proposed commit, including hosted CI. Automated version checks cannot determine whether release notes are complete or accurately attributed.

Build source and wheel distributions with the project interpreter:

```sh
.venv/bin/python -c "import setuptools.build_meta as b; b.build_sdist('dist'); b.build_wheel('dist')"
.venv/bin/python scripts/check-sdist.py dist/icloud_for_agents-VERSION.tar.gz
.venv/bin/python scripts/check-branding-package.py dist/icloud_for_agents-VERSION-py3-none-any.whl
```

Replace the example filenames with the generated names. Install the candidate into a disposable environment, then run the relevant checks from an extracted source archive. Keep the working installation available for rollback. Record which platform and dependency installation were checked; a reused environment does not demonstrate a fresh installation.

Check documentation links and install paths, and review the [maintenance guide](../getting-started/maintenance.md) against the release. Verify that the private vulnerability-reporting route in [SECURITY.md](../../SECURITY.md) is available and monitored.

## Publish and verify

Publish only with the repository owner's authorization. Create the release tag at the exact source commit whose hosted checks passed. The workflow does not rerun for tag pushes, so verify the tag target before publication.

Base the GitHub release notes on the reviewed changelog entry. Confirm that the version, publication date and substantive changes agree. Publishing a GitHub release does not authorize uploading a Python package or changing a user's installed service.

After publication, verify the public release and its tag target. Report the published version separately from any installed or running version. Upgrading a live installation is a separate operation covered by the [maintenance guide](../getting-started/maintenance.md).
