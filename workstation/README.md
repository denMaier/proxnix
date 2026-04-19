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
python3 -m pip install --user --upgrade proxnix-workstation
```

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
- Secret-store mutation and SSH key handling are implemented in Python, with
  `sops` retained at the encryption boundary for wire-format compatibility.
- Release tags are expected to match `[project].version` in `pyproject.toml`.
