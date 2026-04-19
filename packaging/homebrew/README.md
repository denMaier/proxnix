# Homebrew packaging

The Homebrew tap is split into two install surfaces on Apple Silicon Macs:

```bash
brew install denMaier/tap/proxnix-workstation
brew install --cask denMaier/tap/proxnix-manager
```

This repository keeps both scaffolds here so the tap can be updated from the
main release source.

## Files

- `Formula/proxnix-workstation.rb.template` is the parameterized formula template
- `Casks/proxnix-manager.rb.template` is the parameterized cask template
- `../ci/render-homebrew-formula.sh` renders a concrete formula for one tagged release
- `../ci/render-homebrew-cask.sh` renders a concrete cask for one tagged release

## Render a workstation formula

From the repo root:

```bash
./ci/render-homebrew-formula.sh --version 0.1.0
```

Or provide the release tarball digest explicitly:

```bash
./ci/render-homebrew-formula.sh \
  --version 0.1.0 \
  --sha256 <sha256>
```

Write directly into a tap checkout:

```bash
./ci/render-homebrew-formula.sh \
  --version 0.1.0 \
  --output ../homebrew-tap/Formula/proxnix-workstation.rb
```

## Render an app cask

From the repo root:

```bash
./ci/render-homebrew-cask.sh --version 0.1.0
```

Or provide the DMG digest explicitly:

```bash
./ci/render-homebrew-cask.sh \
  --version 0.1.0 \
  --sha256-arm64 <sha256>
```

Write directly into a tap checkout:

```bash
./ci/render-homebrew-cask.sh \
  --version 0.1.0 \
  --output ../homebrew-tap/Casks/proxnix-manager.rb
```

## Recommended tap layout

Create a separate public tap repository. In this setup the live tap is:

```text
denMaier/homebrew-tap
```

The simplest flow is:

1. tag a release in this repo
2. let GitHub Actions upload the DMG assets to the release
3. render the matching workstation formula with `ci/render-homebrew-formula.sh`
4. render the matching app cask with `ci/render-homebrew-cask.sh`
5. commit the rendered files into the tap repo
6. users install with `brew install denMaier/tap/proxnix-workstation` or `brew install --cask denMaier/tap/proxnix-manager`

If you want the shortest possible Homebrew syntax and easy ecosystem
integration, keep the tap on GitHub.
