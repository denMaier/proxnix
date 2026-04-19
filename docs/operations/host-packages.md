# Host Packages

The preferred host-side install path is now a helper-script entrypoint that
installs a Debian package:

- entrypoint: `host/remote/install-host-package.sh`
- package name: `proxnix-host`
- artifact pattern: `proxnix-host_<version>_<arch>.deb`

The helper script keeps the first install to one command, while `apt` or `dpkg`
still own upgrades and removal underneath.

For the tag-driven release flow, see [Releases](releases.md).

## Install the latest tagged release

On the Proxmox host:

```bash
bash -c "$(curl -fsSL https://codeberg.org/maieretal/proxnix/raw/branch/main/host/remote/install-host-package.sh)"
```

Install a specific version:

```bash
bash -c "$(curl -fsSL https://codeberg.org/maieretal/proxnix/raw/branch/main/host/remote/install-host-package.sh)" -- --version 0.1.0
```

The helper script installer:

- resolves the matching `.deb` for the host architecture
- downloads it from the Codeberg package registry
- verifies the checksum when latest-release metadata is available
- installs it with `apt`

## Manual Debian package path

If you want the raw package artifact for offline install, testing, or manual
administration, use the `.deb` directly.

## Build locally

From the repository root:

```bash
./host/packaging/package-deb.sh
```

The package artifact is written to:

```text
dist/proxnix-host_<version>_<arch>.deb
```

## Install on a Proxmox host

Copy the `.deb` to the node and install it with:

```bash
apt install ./proxnix-host_<version>_<arch>.deb
```

Or:

```bash
dpkg -i ./proxnix-host_<version>_<arch>.deb
apt-get install -f
```

The package post-install step verifies `pveversion` and `sops`, ensures the
expected proxnix directories exist, and enables `proxnix-gc.timer`.

## Remove

Remove only the installed host runtime:

```bash
apt remove proxnix-host
```

Or:

```bash
dpkg -r proxnix-host
```

Published relay-cache data remains outside the package payload:

- `/var/lib/proxnix/site.nix`
- `/var/lib/proxnix/containers/`
- `/var/lib/proxnix/private/`
- `/etc/proxnix/host_relay_identity`

## CI publishing

The package can be built and published from the self-hosted Forgejo Actions
workflow at `.forgejo/workflows/host-packages.yml`.

Published package name:

- `proxnix-host-deb`
- `proxnix-host-meta`

Published versions:

- tags publish as the tag name
- non-tag builds publish as `sha-<12-char-commit>`

Tagged releases also refresh `proxnix-host-meta/latest/proxnix-host-latest.env`,
which is what the curl-friendly installer uses to resolve the latest stable
host package.
