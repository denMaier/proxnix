# Homebrew packaging

`ProxnixManager` is intended to ship through a dedicated tap so users can
install it with a single command:

```bash
brew install denMaier/tap/proxnix-manager
```

This repository keeps the formula scaffold here so the tap can be updated from
the main release source.

## Files

- `Formula/proxnix-manager.rb.template` is the parameterized formula template
- `../ci/render-homebrew-formula.sh` renders a concrete formula for one tagged release

## Render a release formula

From the repo root:

```bash
./ci/render-homebrew-formula.sh --version 0.1.0 --sha256 <sha256>
```

Or let the script fetch the tarball and compute the digest:

```bash
./ci/render-homebrew-formula.sh --version 0.1.0
```

Write directly into a tap checkout:

```bash
./ci/render-homebrew-formula.sh \
  --version 0.1.0 \
  --output ../homebrew-tap/Formula/proxnix-manager.rb
```

## Recommended tap layout

Create a separate public tap repository. In this setup the live tap is:

```text
denMaier/homebrew-tap
```

The simplest flow is:

1. tag a release in this repo
2. render the matching formula with `ci/render-homebrew-formula.sh`
3. commit the rendered formula into the tap repo
4. users install with `brew install denMaier/tap/proxnix-manager`

If you want the shortest possible Homebrew syntax and easy ecosystem
integration, publish the tap on GitHub. A Codeberg-hosted tap also works, but
GitHub-hosted taps fit Homebrew’s shorthand and surrounding tooling better.
