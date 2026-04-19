# Workstation Packages

The workstation tools are built and published from the GitHub Actions workflow
at `.github/workflows/pypi-publish.yml`.

For the tag-driven release flow, see [Releases](releases.md).

## What the workflow builds

- Python source distribution
- Python wheel

Branch pushes to `main`, matching `v*` tags, and manual dispatches trigger the
workflow. Pull requests build but do not publish. Tagged releases publish to
PyPI.

## Install

Preferred install:

```bash
pip install proxnix-workstation
```

Repo helper:

```bash
./ci/install-workstation.sh
```

That single package installs both the workstation CLI and the terminal UI:

- `proxnix`
- `proxnix-tui`

Compatibility aliases such as `proxnix-publish` and `proxnix-secrets` are
installed too.

Runtime requirements still need to exist on the workstation:

- `sops`
- `ssh`
- `rsync`

## PyPI publishing

Tagged releases publish `proxnix-workstation` to PyPI.
This repo uses PyPI Trusted Publishing from GitHub Actions rather than a stored
API token.

The workflow validates that the pushed `v*` tag matches
`workstation/pyproject.toml`:

```text
v1.2.3  <->  version = "1.2.3"
```

## Local build

Build the package locally from `workstation/`:

```bash
uv build
```

Artifacts are written to:

```text
workstation/dist/
```

## NixOS and nix-darwin

This repo now exports workstation packages and a shared module via
`workstation/flake.nix`.

Package outputs:

- `./workstation#proxnix-workstation` for TUI + CLI
- `./workstation#proxnix-workstation-cli` for CLI only

Module outputs:

- `inputs.proxnix.nixosModules.proxnix-workstation`
- `inputs.proxnix.darwinModules.proxnix-workstation`

## macOS app

`ProxnixManager` is distributed separately through a Homebrew tap. See
[ProxnixManager](proxnix-manager.md).

### NixOS example

```nix
{
  inputs.proxnix.url = "path:/path/to/proxnix/workstation";

  outputs = { self, nixpkgs, proxnix, ... }: {
    nixosConfigurations.my-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        proxnix.nixosModules.proxnix-workstation
        ({ ... }: {
          proxnix.workstation.enable = true;
        })
      ];
    };
  };
}
```

### nix-darwin example

```nix
{
  inputs.proxnix.url = "path:/path/to/proxnix/workstation";

  outputs = { self, nix-darwin, proxnix, ... }: {
    darwinConfigurations.mac = nix-darwin.lib.darwinSystem {
      system = "aarch64-darwin";
      modules = [
        proxnix.darwinModules.proxnix-workstation
        ({ ... }: {
          proxnix.workstation.enable = true;
        })
      ];
    };
  };
}
```
