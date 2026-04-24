# ProxnixManager

`ProxnixManager` is the Electrobun GUI for proxnix. It provides a graphical
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

- `python@3.12`
- `sops`

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
