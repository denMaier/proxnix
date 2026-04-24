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

The app ships a Python bridge script alongside the Bun process. The bridge can:

- read and write `~/.config/proxnix/config`
- preserve unknown `PROXNIX_*` assignments
- scan the site repo for containers, drop-ins, secret groups, and identities
- call the workstation Python package for secrets, doctor, and publish actions
