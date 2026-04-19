# Releases

The ergonomic release path is:

1. install the repo-managed git hooks once
2. run one release command with `patch`, `minor`, or `major`
3. let Forgejo publish the host package and the workstation Python package from the pushed tag

## Install the git hooks

From the repo root:

```bash
./ci/install-git-hooks.sh
```

This sets:

```text
core.hooksPath = .githooks
```

Current hook coverage:

- `pre-push` validates release tags before they are pushed
- only tags matching `vMAJOR.MINOR.PATCH` or a prerelease suffix are accepted
- release tags must be annotated, not lightweight
- release tags cannot be deleted or silently moved by push
- release tags must point to a commit reachable from the release branch
- release tags must match both `VERSION` and `workstation/pyproject.toml`

Default release branch:

```text
main
```

Override it when needed with:

```bash
RELEASE_BRANCH=stable git push origin refs/tags/v1.2.3
```

## One-command release

Run:

```bash
./ci/release.sh patch
```

Examples:

```bash
./ci/release.sh patch
./ci/release.sh minor
./ci/release.sh major --no-push
./ci/release.sh --version 1.2.3-rc1
```

`ci/release.sh`:

- reads the current version from `VERSION`
- bumps `major`, `minor`, or `patch` when asked
- updates `VERSION`
- updates `workstation/pyproject.toml`
- creates a release commit
- creates an annotated `v*` tag
- pushes the commit and tag by default

Default commit and tag message:

```text
Release v0.1.1
```

If you need finer control, the lower-level flow is still available through
`./ci/bump-version.sh`, `./ci/set-version.sh`, and `./ci/release-tag.sh`.

## Version-only bump

If you want to update the version files without committing or tagging yet, run:

```bash
./ci/bump-version.sh patch
```

Examples:

```bash
./ci/bump-version.sh patch
./ci/bump-version.sh minor
./ci/bump-version.sh major
```

This only updates:

- `VERSION`
- `workstation/pyproject.toml`

## What the tag triggers

Pushing a matching `v*` tag triggers:

- [Host Packages](host-packages.md)
- [Workstation Packages](workstation-packages.md)

Those workflows publish artifacts using the tag as the package version.

## Local dry runs

Preview the tag creation flow without mutating git state:

```bash
./ci/release.sh patch --dry-run
```
