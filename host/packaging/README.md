# proxnix host packaging

Build the Debian package from the repository root:

```bash
./host/packaging/package-deb.sh
```

The package artifact is written to:

```text
dist/proxnix-host_<version>_<arch>.deb
```

Install it on a Proxmox host with:

```bash
apt install ./dist/proxnix-host_<version>_<arch>.deb
```

It installs the same host runtime assets as `host/install.sh`, but the package
manager owns file tracking, upgrades, and uninstall.
