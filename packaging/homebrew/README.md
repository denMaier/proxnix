# Homebrew packaging

`ProxnixManager` is intended to ship through a dedicated tap so users can
install it with a single command on Apple Silicon Macs:

```bash
brew install --cask denMaier/tap/proxnix-manager
```

This repository keeps the cask scaffold here so the tap can be updated from the
main release source.

## Files

- `Casks/proxnix-manager.rb.template` is the parameterized cask template
- `../ci/render-homebrew-cask.sh` renders a concrete cask for one tagged release

## Render a release cask

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
3. render the matching cask with `ci/render-homebrew-cask.sh`
4. commit the rendered cask into the tap repo
5. users install with `brew install --cask denMaier/tap/proxnix-manager`

If you want the shortest possible Homebrew syntax and easy ecosystem
integration, keep the tap on GitHub.
