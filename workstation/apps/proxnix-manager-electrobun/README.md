<p align="center">
  <img src="assets/proxnix-icon.png" alt="Proxnix icon" width="128" height="128">
</p>

# Proxnix Manager

This is the supported `Proxnix Manager` GUI. It replaces the retired macOS-only
Swift app with a cross-platform Electrobun app.

Current scope:

- first-run onboarding and site scaffolding
- workstation settings
- site scanning and container bundle management
- shared, group, and container secrets
- git status, staging, commit, and push
- doctor and publish workflows

## Commands

From this directory:

```bash
bun install
bun run typecheck
```

The usual runtime command is:

```bash
bun start
```

The dev bridge prefers the repo-local workstation virtualenv, so prepare it
before testing secret providers:

```bash
../../../ci/bootstrap-workstation-venv.sh
../../../workstation/.venv/bin/python -m pip install pykeepass
```

Use a disposable config home to test onboarding without touching your real
workstation config:

```bash
XDG_CONFIG_HOME=/tmp/proxnix-onboarding-config bun start
```

## Layout

```text
src/
  bun/
    index.ts
    workstationBridge.ts
    scripts/proxnix_bridge.py
  mainview/
    index.ts
    index.html
    index.css
  shared/
    types.ts
```

## macOS signing

Release builds can be Developer ID signed and notarized by setting:

```bash
PROXNIX_MANAGER_MACOS_CODESIGN=1
PROXNIX_MANAGER_MACOS_NOTARIZE=1
ELECTROBUN_DEVELOPER_ID="Developer ID Application: ..."
ELECTROBUN_TEAMID="..."
ELECTROBUN_APPLEID="..."
ELECTROBUN_APPLEIDPASS="..."
```

Unsigned local builds are ad-hoc signed by default so macOS can usually put
them on the Privacy & Security "Open Anyway" path. They may still need
quarantine removed before macOS will open them:

```bash
xattr -dr com.apple.quarantine "/Applications/Proxnix Manager.app"
```

Set `PROXNIX_MANAGER_MACOS_ADHOC_SIGN=0` to disable the ad-hoc fallback.

## Bridge design

The app is CLI-first from the UI boundary. Bun invokes a Python bridge process
by subprocess and receives JSON envelopes; it does not import workstation
Python internals directly. The bridge can:

- read and write `~/.config/proxnix/config`
- preserve unknown `PROXNIX_*` assignments
- scan the site repo for containers, drop-ins, secret groups, and identities
- call the workstation Python package for secrets, doctor, and publish actions

Python resolution order:

1. `PROXNIX_MANAGER_PYTHON`
2. packaged `bin/proxnix-python` under the app resources directory
3. repo-local `workstation/.venv/bin/python`
4. Homebrew `python`
5. `python3`, `python`, or `py -3`

Packaged builds bundle the workstation source and core Python dependencies
under the app resources directory. Optional provider packages such as
`pykeepass` are not bundled.

Set `PROXNIX_MANAGER_PYTHONPATH` in the app settings or process environment to
add extra import paths for Manager-only Python integrations. The value uses the
platform `PYTHONPATH` separator and is applied to the bridge plus CLI
subprocesses launched by the bridge. Prefer `site-packages` paths; venv `bin`
paths and venv Python executables are expanded automatically.
