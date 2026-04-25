# Proxnix Workstation

This directory contains workstation-side packages and packaging infrastructure.

## Layout

```text
workstation/
  cli/          Python CLI/TUI package, repo-local wrappers, tests, legacy helper
  manager/      Proxnix Manager desktop app and hosted web UI
  nix/          Nix package and module definitions
  packaging/    release artifact builders shared by CLI and Manager packaging
  flake.nix     Nix package and module exports
```

Generated local-only paths:

- `.venv/` for the repo-local development virtualenv
- `.uv-cache/` for uv downloads/build cache
- `dist/` for built artifacts

Use `./ci/bootstrap-workstation-venv.sh` to prepare the repo-local Python
environment. The development CLI wrappers live under `workstation/cli/bin/`.
