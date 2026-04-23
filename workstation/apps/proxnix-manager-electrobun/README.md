# proxnix-manager-electrobun

This is the in-progress Electrobun replacement for the macOS-only Swift
`ProxnixManager` app.

Current scope:

- Electrobun app shell
- typed RPC between Bun and the renderer
- repo-local Python bridge for workstation config + site scanning
- working Settings + Containers screens

Current blocker on this machine:

- the corporate MITM proxy breaks Electrobun's binary download step

That means this app can be scaffolded, installed from Bun's local cache, and
typechecked, but `bun start` will still fail until the Electrobun core binary
download is made to trust the local proxy CA or the binaries are vendored.

## Commands

From this directory:

```bash
bun install --offline
bun run typecheck
```

The usual runtime command is:

```bash
bun start
```

On this workstation that remains blocked by the proxy certificate issue.

## Layout

```text
src/
  bun/
    index.ts
    workstationBridge.ts
    scripts/proxnix_bridge.py
  mainview/
    index.ts
    index.html
    index.css
  shared/
    types.ts
```

## Bridge design

The first slice does not depend on the publishable Python workstation package
being installed. Instead it ships a very small Python bridge script alongside
the Bun process and uses only the standard library to:

- read and write `~/.config/proxnix/config`
- preserve unknown `PROXNIX_*` assignments
- scan the site repo for containers, drop-ins, secret groups, and identities

That keeps development moving while the network environment is hostile.
