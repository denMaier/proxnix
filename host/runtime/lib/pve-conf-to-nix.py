#!/usr/bin/env python3
"""Compatibility entrypoint for proxnix-host pve-conf-to-nix."""

from __future__ import annotations

import os
import shutil
import sys


def main() -> int:
    configured = os.environ.get("PROXNIX_HOST_BIN")
    if configured:
        candidates = [configured]
    else:
        candidates = [
            "/nix/var/nix/profiles/proxnix-host/bin/proxnix-host",
            shutil.which("proxnix-host"),
        ]

    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            os.execv(candidate, [candidate, "pve-conf-to-nix", *sys.argv[1:]])

    print(
        "ERROR: proxnix-host not found; install the proxnix host profile or set PROXNIX_HOST_BIN",
        file=sys.stderr,
    )
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
