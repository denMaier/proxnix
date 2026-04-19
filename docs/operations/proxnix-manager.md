# ProxnixManager

`ProxnixManager` is the macOS GUI for proxnix. The intended install surface is
a Homebrew tap so Apple Silicon users can install it with one command:

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

## Cask source

This repository keeps the tap scaffold here:

```text
packaging/homebrew/
```

Relevant files:

- `packaging/homebrew/Casks/proxnix-manager.rb.template`
- `ci/render-homebrew-cask.sh`
- `.github/workflows/proxnix-manager-dmg.yml`

## Release flow

1. tag a proxnix release in this repo
2. let GitHub Actions upload the matching DMG assets to the GitHub release
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
