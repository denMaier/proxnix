<p align="center">
  <img src="../assets/proxnix-icon.png" alt="Proxnix icon" width="96" height="96">
</p>

# Proxnix Manager

`Proxnix Manager` is the Electrobun GUI for Proxnix. It provides a graphical
interface for the most common workstation workflows:

- first-run onboarding and site scaffolding
- workstation settings
- site scanning and container bundle management
- shared, group, and container secret management
- git status, staging, commit, and push
- doctor and publish workflows

The app source lives in `workstation/apps/proxnix-manager-electrobun/`.

The intended macOS install surface is a Homebrew tap so Apple Silicon users can
install it with one command:

```bash
brew install --cask denMaier/tap/proxnix-manager
```

## What the cask installs

The Homebrew cask is designed to install:

- `Proxnix Manager.app` into `/Applications`
- a bundled script directory inside the app bundle so the GUI can find
  `proxnix-publish`, `proxnix-secrets`, and related commands without extra
  manual path setup
- the arm64 DMG release asset for the matching proxnix tag

The cask depends on these Homebrew formulae:

- `python`
- `sops`

The app bundle includes the workstation Python package source, the CLI wrapper
scripts, and the core Python modules needed by the built-in workflows. It does
not bundle optional secret-provider SDKs such as `pykeepass`; users who choose
those providers install the corresponding Python package into the Python
runtime used by the Manager.

Runtime tools still expected from the machine:

- `ssh`
- `rsync`

Current target platform:

- Apple Silicon (`arm64`) macOS
- Linux (`x64`) release archive

## macOS signing and notarization

Unsigned macOS builds are useful for local testing, but general release DMGs
should be Developer ID signed and notarized. Electrobun signing is enabled by
environment variables during the macOS package build:

```bash
PROXNIX_MANAGER_MACOS_CODESIGN=1
PROXNIX_MANAGER_MACOS_NOTARIZE=1
ELECTROBUN_DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
ELECTROBUN_TEAMID="TEAMID"
ELECTROBUN_APPLEID="apple-id@example.com"
ELECTROBUN_APPLEIDPASS="app-specific-password"
```

The GitHub Actions DMG workflow imports a `Developer ID Application`
certificate when these repository secrets are present:

- `MACOS_DEVELOPER_ID_APPLICATION_P12_BASE64`
- `MACOS_DEVELOPER_ID_APPLICATION_P12_PASSWORD`
- `MACOS_KEYCHAIN_PASSWORD`
- `ELECTROBUN_DEVELOPER_ID`
- `ELECTROBUN_TEAMID`
- `ELECTROBUN_APPLEID`
- `ELECTROBUN_APPLEIDPASS`

If the certificate secrets are missing, CI still builds an unsigned test DMG.
Unsigned macOS builds are ad-hoc signed by default so they can usually be
opened through the Privacy & Security "Open Anyway" approval path. If macOS
still refuses to open an unsigned local/test install, remove quarantine:

```bash
xattr -dr com.apple.quarantine "/Applications/Proxnix Manager.app"
```

## Python runtime resolution

`Proxnix Manager` is CLI-first at runtime. The Bun bridge invokes Python only to
run the bundled bridge script and workstation CLI commands, and the bridge
communicates with the UI through structured JSON.

Interpreter selection is:

1. `PROXNIX_MANAGER_PYTHON`, when explicitly set
2. bundled `bin/proxnix-python` under the packaged app resources directory
3. repo-local `workstation/.venv/bin/python` during development
4. Homebrew `python`
5. `python3`, `python`, or Windows `py -3` from `PATH`

Advanced imports can be added with `PROXNIX_MANAGER_PYTHONPATH`, either in the
shared proxnix config or in the Manager process environment. This is scoped to
the Manager bridge and its workstation CLI subprocesses. Use it for local or
third-party provider modules that are not bundled with the app; do not use it
as the normal way to find `proxnix_workstation`. A
`site-packages` path is the direct form; venv `bin` directories and venv Python
executables are accepted and expanded to the matching `site-packages` directory.

For local development, prepare the repo-local environment before running the
app:

```bash
./ci/bootstrap-workstation-venv.sh
```

For the `pykeepass` provider, install `pykeepass` separately into the runtime
used by the Manager, for example:

```bash
workstation/.venv/bin/python -m pip install pykeepass
```

For an installed packaged Manager, prefer a dedicated Python 3.12-or-newer provider venv
instead of installing optional provider packages into Homebrew Python globally:

```bash
/opt/homebrew/opt/python/libexec/bin/python3 -m venv ~/.local/share/proxnix/provider-python
~/.local/share/proxnix/provider-python/bin/python -m pip install pykeepass
```

Then set `Manager Python path` in the app settings to:

```text
~/.local/share/proxnix/provider-python/bin
```

The bridge expands venv `bin` paths to the matching `site-packages` directory
before launching workstation CLI subprocesses.

## Cask source

This repository keeps the tap scaffold here:

```text
packaging/homebrew/
```

Relevant files:

- `packaging/homebrew/Casks/proxnix-manager.rb.template`
- `ci/render-homebrew-cask.sh`
- `.github/workflows/proxnix-manager-dmg.yml`
- `.github/workflows/proxnix-manager-linux.yml`

## Release flow

1. tag a proxnix release in this repo
2. let GitHub Actions upload the matching app assets to the GitHub release
3. render the concrete cask for that tag
4. commit the rendered file into your tap repo
5. users install or upgrade with Homebrew

Example:

```bash
./ci/render-homebrew-cask.sh \
  --version 0.1.0 \
  --output ../homebrew-tap/Casks/proxnix-manager.rb
```

The release workflows create `workstation/.venv` with Python 3.12 and install
the core runtime dependencies before wrapping the Electrobun app. The post-wrap
step copies the workstation source and core Python dependencies into the app
resources directory, then writes bundled CLI wrappers under its `bin/`
subdirectory. Optional providers such as `pykeepass` are intentionally excluded
from the shipped app bundle.

Local package tests should use a Python 3.12-or-newer environment for the
copied dependencies because the packaged `proxnix-python` wrapper runs
Homebrew `python` when it is available. If the repo-local `workstation/.venv`
uses an older Python version, create a temporary Python 3.12-or-newer venv and
pass it as `PROXNIX_PACKAGE_PYTHON`.

## Recommended repository setup

The live tap repo is:

```text
denMaier/homebrew-tap
```

If you want the shortest Homebrew syntax and the least friction with the wider
ecosystem, publish the tap on GitHub. A public GitHub mirror of the main
`proxnix` repo is also useful because:

- community Proxmox helper-script ecosystems expect GitHub-friendly raw URLs
- Homebrew tap shorthand works best with GitHub-hosted taps

Without that setup, the cask scaffold in this repo is still usable, but the
final tap publication step remains external.
