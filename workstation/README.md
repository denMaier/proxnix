# proxnix-workstation

`proxnix-workstation` packages the workstation-side proxnix tools as a normal
Python distribution that can be published to PyPI and installed with `pip`.

It requires Python 3.12 or newer.

It installs these user-facing commands:

- `proxnix`
- `proxnix-secrets`
- `proxnix-publish`
- `proxnix-doctor`
- `proxnix-tui`
- `proxnix-lxc-exercise`

`proxnix` is the preferred entrypoint. The split commands remain available as
compatibility aliases.

## Layout

```text
workstation/
├── apps/          native UI projects
├── bin/           repo-local command wrappers for development
├── legacy/        shell-era compatibility helpers
├── nix/           Nix package and module definitions
├── packaging/     release artifact builders
├── src/           publishable Python package source
├── pyproject.toml
└── flake.nix
```

If you are working from the repository rather than an installed package, use
the wrappers under `workstation/bin/`.

Generated local-only paths:

- `.venv/` for a development virtualenv
- `dist/` for built Python artifacts
- `.tmp-workstation-packaging/` for packaging scratch space

## Install

```bash
pip install proxnix-workstation
```

That installs both the workstation CLI and the terminal UI:

- `proxnix`
- `proxnix-tui`

The split commands remain available too:

- `proxnix-publish`
- `proxnix-secrets`
- `proxnix-doctor`
- `proxnix-lxc-exercise`

Or with the repo helper:

```bash
./ci/install-workstation.sh
```

If you want repo-local tooling instead of touching the global Python
environment:

```bash
./ci/bootstrap-workstation-venv.sh
```

That prepares `workstation/.venv` plus the wrappers under `workstation/bin/`.

Python dependencies are bundled through the package, but these external tools
must still be available on the machine:

- `sops`
- `ssh`
- `rsync`

The commands read the same workstation config as the existing shell-based
workflow:

```text
~/.config/proxnix/config
```

Expected settings include:

- `PROXNIX_SITE_DIR`
- `PROXNIX_MASTER_IDENTITY`
- `PROXNIX_HOSTS`
- `PROXNIX_SSH_IDENTITY` (optional)

For source-secret retrieval, the workstation also supports:

- `PROXNIX_SECRET_PROVIDER`
- `PROXNIX_SECRET_PROVIDER_COMMAND` when `PROXNIX_SECRET_PROVIDER=exec`

## Secret Providers

Runtime publish artifacts always stay SOPS-based. The configurable part is the
workstation source-secret backend used by `proxnix-secrets`,
`proxnix-publish`, and `proxnix-doctor`.

Built-in provider names:

- `embedded-sops`
- `pass`
- `gopass`
- `passhole`
- `pykeepass`
- `keepassxc-cli` and `keepassxc`
- `op`, `1password`, `onepassword`
- `bws`, `bitwarden-secrets`
- `vault`, `vault-kv`
- `infisical`
- `exec`

Example:

```bash
export PROXNIX_SECRET_PROVIDER=passhole
export PROXNIX_PASSHOLE_DATABASE=~/.local/share/passhole/proxnix.kdbx
export PROXNIX_PASSHOLE_PASSWORD_FILE=~/.config/proxnix/passhole-password
```

Or:

```bash
export PROXNIX_SECRET_PROVIDER=pykeepass
export PROXNIX_PYKEEPASS_DATABASE=~/.local/share/keepass/proxnix.kdbx
export PROXNIX_PYKEEPASS_PASSWORD_FILE=~/.config/proxnix/keepass-password
```

## Examples

```bash
proxnix secrets set 120 db_password
proxnix publish
proxnix doctor --site-only
proxnix tui
proxnix exercise lxc --host root@node1 --base-vmid 940
```

## Build

Build source and wheel distributions from the `workstation/` directory:

```bash
uv build
```

Artifacts are written to:

```text
workstation/dist/
```

## Publish

Tagged releases publish the package from Forgejo Actions.

For a local manual publish to PyPI:

```bash
python3 -m pip install --user --upgrade twine
python3 -m twine upload dist/*
```

## Notes

- The package intentionally keeps `sops`, `ssh`, and `rsync` as external
  system tools.
- Some optional secret providers also require their own external tools or
  Python packages. For example, `keepassxc-cli`, `op`, `bws`, `vault`, and
  `infisical` expect their respective CLIs to be installed, while
  `pykeepass` expects the `pykeepass` Python package to be available.
- Secret-store mutation and SSH key handling are implemented in Python, with
  `sops` retained at the encryption boundary for wire-format compatibility.
- Release tags are expected to match `[project].version` in `pyproject.toml`.
- `ProxnixManager` is intended to ship separately from a Homebrew tap; see
  `../docs/operations/proxnix-manager.md`.
