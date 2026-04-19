# ProxnixManager

`ProxnixManager` is the macOS GUI for proxnix. The intended install surface is
a Homebrew tap so users can install it with one command:

```bash
brew install <owner>/proxnix/proxnix-manager
```

## What the formula installs

The Homebrew formula is designed to install:

- the `ProxnixManager` app launcher as `proxnix-manager`
- the workstation CLI commands from `proxnix-workstation`
- a bundled script directory inside the app bundle so the GUI can find
  `proxnix-publish`, `proxnix-secrets`, and related commands without extra
  manual path setup

Runtime tools still expected from the machine:

- `ssh`
- `rsync`
- `sops`

## Formula source

This repository keeps the tap scaffold here:

```text
packaging/homebrew/
```

Relevant files:

- `packaging/homebrew/Formula/proxnix-manager.rb.template`
- `ci/render-homebrew-formula.sh`

## Release flow

1. tag a proxnix release in this repo
2. render the concrete formula for that tag
3. commit the rendered file into your tap repo
4. users install or upgrade with Homebrew

Example:

```bash
./ci/render-homebrew-formula.sh \
  --version 0.1.0 \
  --output ../homebrew-proxnix/Formula/proxnix-manager.rb
```

## Recommended repository setup

Use a dedicated public tap repository, ideally named `homebrew-proxnix`.

If you want the shortest Homebrew syntax and the least friction with the wider
ecosystem, publish the tap on GitHub. A public GitHub mirror of the main
`proxnix` repo is also useful because:

- community Proxmox helper-script ecosystems expect GitHub-friendly raw URLs
- Homebrew tap shorthand works best with GitHub-hosted taps

Without that setup, the formula scaffold in this repo is still usable, but the
final tap publication step remains external.
