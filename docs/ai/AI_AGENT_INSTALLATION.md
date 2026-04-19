# AI agent installation

This file is the operator playbook for deploying proxnix.
Use it instead of scanning the repo.

## Goal

Install proxnix on one or more Proxmox nodes, set up the workstation side, and
either stop there for a fresh bootstrap or continue into the full
workstation-owned site repo publish flow.

For fresh deployments, prefer running the real proxnix exercise harness after
publish so the agent gets a report with concrete failures instead of stopping at
"commands exited 0".

Do not guess the operator's preferences. Ask first, then choose the supported
path.

## Ask these questions first

Ask these in one short batch before changing anything:

1. How many Proxmox nodes should receive proxnix right now: one node or multiple nodes?
2. Which host install path do you want: the published Debian package, a local repo checkout on the node, or Ansible from a control machine?
3. Do you want `host-bootstrap` only for now, or the full workstation publish flow?
4. Do you already have a workstation site repo, or should I create a new one?
5. Do you want a full secrets-capable setup now, or a config-only bootstrap first?
6. What SSH targets should I publish to, should I use a dedicated publish SSH identity, and what should I use as the SOPS master identity?
7. Do you want me to stop at host validation, or also run the proxnix exercise harness for an end-to-end report?
8. If I run the exercise harness, which Proxmox host should I target and which base VMID should I reserve for the disposable test containers?

If the answer is already available from local config or the current task
context, present it as the proposed default and ask for confirmation instead of
asking blindly.

## Normalize the answers

Convert the answers into this working state before executing:

- `install_scope = single-node | multi-node`
- `install_method = deb-package | local-checkout | ansible`
- `deployment_goal = host-bootstrap | full-publish`
- `site_repo = existing | create-new`
- `publish_mode = full | config-only`
- `hosts = root@node1 root@node2 ...`
- `ssh_identity = optional path`
- `verification_mode = host-only | publish | exercise`
- `exercise_host = publish target such as root@node1 when verification_mode=exercise`
- `exercise_base_vmid = starting numeric VMID when verification_mode=exercise`

## Decision rules

Use these defaults unless the user asked for something else:

- Prefer `deb-package` for a normal single-node or small-cluster install.
- Prefer `ansible` when the user explicitly wants one command from a control
  machine across multiple nodes.
- Use `local-checkout` only when the agent is already running on the target
  Proxmox host or the user explicitly wants the repo-local installer.
- Prefer `host-bootstrap` when the user wants a fresh start with the current
  repo state but plans to publish a real site repo later by hand.
- Prefer `full` publish mode unless the user wants to defer SOPS and identities.
- Prefer `publish` verification at minimum.
- Prefer `exercise` verification for a first deployment, cluster rollout, or any
  situation where the agent should hand back a concrete pass/fail report.
- Use `--config-only` only when the operator explicitly wants to defer secret
  stores and identities, or when the change is known to touch config only.

## Preferred automation path

When the chosen install method is Ansible, do not assemble the deployment out
of ad hoc shell snippets. Pick the playbook that matches the approved goal.

### For `deployment_goal=host-bootstrap`

Prefer:

- `host/ansible/ai-agent-bootstrap.yml`
- `host/ansible/ai-agent-bootstrap.vars.example.yml`

That playbook:

- installs proxnix on the target Proxmox hosts by importing `host/ansible/install.yml`
- runs `proxnix-doctor --host-only` on every target node
- renders the workstation config used by `proxnix`
- optionally creates an empty site repo skeleton
- optionally runs `proxnix exercise lxc`
- does not validate or publish the live site repo

Typical usage:

```bash
cp host/ansible/ai-agent-bootstrap.vars.example.yml host/ansible/ai-agent-bootstrap.vars.yml
ansible-playbook \
  -i host/inventory.proxmox.ini \
  host/ansible/ai-agent-bootstrap.yml \
  -e @host/ansible/ai-agent-bootstrap.vars.yml
```

### For `deployment_goal=full-publish`

Prefer:

- `host/ansible/ai-agent-deploy.yml`
- `host/ansible/ai-agent-deploy.vars.example.yml`

That playbook:

- installs proxnix on the target Proxmox hosts by importing `host/ansible/install.yml`
- writes the workstation config used by `proxnix`
- initializes the host relay identity when needed
- validates the site repo
- runs publish dry-run plus real publish
- runs remote drift checks with `proxnix doctor`
- optionally runs `proxnix exercise lxc` and leaves a Markdown and JSON report

Typical usage:

```bash
cp host/ansible/ai-agent-deploy.vars.example.yml host/ansible/ai-agent-deploy.vars.yml
ansible-playbook \
  -i host/inventory.proxmox.ini \
  host/ansible/ai-agent-deploy.yml \
  -e @host/ansible/ai-agent-deploy.vars.yml
```

If the user chose `deb-package` or `local-checkout` instead of Ansible, keep
using the supported host install path, then follow either the host-bootstrap or
full-publish workstation steps that match the approved goal.

## Execution flow

### 1. Install proxnix on every target Proxmox node

Every node that may run proxnix-managed containers needs the host runtime
installed locally.

#### If `install_method=deb-package`

Run on each Proxmox node:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)"
```

For a pinned version:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/denMaier/proxnix/main/host/remote/install-host-package.sh)" -- --version 0.1.0
```

#### If `install_method=local-checkout`

From a checkout of this repo on the Proxmox node, as `root`:

```bash
host/install.sh
```

Use `--dry-run` first if the user wants a preview:

```bash
host/install.sh --dry-run
```

#### If `install_method=ansible`

If `deployment_goal=host-bootstrap`, prefer:

```bash
ansible-playbook \
  -i host/inventory.proxmox.ini \
  host/ansible/ai-agent-bootstrap.yml \
  -e @host/ansible/ai-agent-bootstrap.vars.yml
```

If `deployment_goal=full-publish`, prefer:

```bash
ansible-playbook \
  -i host/inventory.proxmox.ini \
  host/ansible/ai-agent-deploy.yml \
  -e @host/ansible/ai-agent-deploy.vars.yml
```

If the agent only needs the host runtime install and will do the workstation
steps separately, the lower-level playbook is still available:

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml
```

To target a different inventory group:

```bash
ansible-playbook -i host/inventory.proxmox.ini host/ansible/install.yml -e proxnix_target_hosts=proxmox_cluster
```

### 2. Verify the host install immediately

On each target Proxmox node:

```bash
proxnix-doctor --host-only
```

Do not continue if this fails. Fix the host install first.

### 3. Prepare the workstation side

Install the workstation CLI if it is not already available.

Preferred global install:

```bash
python3 -m pip install --user --upgrade proxnix-workstation
```

If the user wants to use the repo-local helper instead:

```bash
./ci/install-workstation.sh
```

If the workstation should avoid mutating the global Python environment, prefer a
repo-local virtualenv plus the wrappers under `workstation/bin/`:

```bash
./ci/bootstrap-workstation-venv.sh
```

That gives the agent a repo-local `ansible-playbook` plus the current repo
version of `proxnix` without depending on global `pip`.

#### If `site_repo=create-new`

Create a separate workstation-owned repo. Use this baseline layout:

```text
proxnix-site/
├── site.nix
├── containers/
│   └── <vmid>/
│       └── dropins/
└── private/
    ├── host_relay_identity.sops.yaml
    ├── shared/
    │   └── secrets.sops.yaml
    ├── groups/
    │   └── <group>/
    │       └── secrets.sops.yaml
    └── containers/
        └── <vmid>/
            ├── age_identity.sops.yaml
            └── secrets.sops.yaml
```

Do not use this install repo as the long-term source of truth for live site
data unless the user explicitly asks for that.

#### Configure `~/.config/proxnix/config`

Write:

```bash
mkdir -p ~/.config/proxnix
cat > ~/.config/proxnix/config << 'EOF'
PROXNIX_SITE_DIR=~/src/proxnix-site
PROXNIX_MASTER_IDENTITY=~/.ssh/proxnix-master
PROXNIX_HOSTS="root@node1 root@node2"
# Optional when SSH config or the agent already handles auth:
# PROXNIX_SSH_IDENTITY=~/.ssh/id_ed25519
EOF
```

Adjust the values to match the answers from the user.

Show the normalized config and confirm it matches the answers:

```bash
proxnix config show
```

If `publish_mode=full`, make sure the configured master identity file exists.
If the user wants a dedicated key and it does not exist yet, create one:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/proxnix-master
```

If the agent is using `host/ansible/ai-agent-deploy.yml`, this config render is
already handled by the playbook.

If the approved goal is `host-bootstrap`, you can stop after rendering and
confirming this config plus the host-only doctor checks. The user can publish
their real site repo later by hand.

### 4. Initialize secrets state when `publish_mode=full`

First validate the workstation-side repo shape:

```bash
proxnix doctor --site-only
```

Initialize the shared host relay identity:

```bash
proxnix secrets init-host-relay
```

If the user wants the shared admin password set now, ask them for the password
hash or generate one with:

```bash
mkpasswd -m sha-512
```

Then store it:

```bash
proxnix secrets set-shared common_admin_password_hash
```

If the user wants a specific container identity created before any secrets are
written:

```bash
proxnix secrets init-container <vmid>
```

### 5. Skip secret setup when `publish_mode=config-only`

Use the config-only workflow when the user explicitly wants to defer SOPS,
identities, or secret stores.

Validate only the config tree:

```bash
proxnix doctor --site-only --config-only
```

### 6. Dry-run the publish

For full mode:

```bash
proxnix publish --dry-run --report-changes
```

For config-only mode:

```bash
proxnix publish --dry-run --report-changes --config-only
```

If the user wants to target one host first, append the host:

```bash
proxnix publish --dry-run --report-changes root@node1
```

Do not continue until the dry-run output matches the intended scope.

### 7. Publish to the relay hosts

For full mode:

```bash
proxnix publish
```

For config-only mode:

```bash
proxnix publish --config-only
```

Do not use `--config-only` immediately after creating or changing secrets or
identities. That mode skips compiled secret stores and relay-encrypted
identities by design.

To publish to only one relay host first:

```bash
proxnix publish root@node1
```

### 8. Verify publish drift

For full mode:

```bash
proxnix doctor root@node1
```

For config-only mode:

```bash
proxnix doctor --config-only root@node1
```

Run this for every target host when the user wants full verification across the
cluster.

## Exercise harness verification

Prefer this over a one-off manual canary CT. It exercises the real publish path,
creates disposable LXCs, waits for first-boot convergence, runs host and guest
assertions, and writes reports an agent can summarize back to the operator.

Run:

```bash
workstation/bin/proxnix --config ~/.config/proxnix/config exercise lxc --host root@node1 --base-vmid 940
```

Or with the installed CLI:

```bash
proxnix --config ~/.config/proxnix/config exercise lxc --host root@node1 --base-vmid 940
```

Useful overrides:

```bash
proxnix --config ~/.config/proxnix/config exercise lxc \
  --host root@node1 \
  --base-vmid 950 \
  --template local:vztmpl/nixos-25.11-x86_64-linux.tar.xz \
  --storage local-lvm \
  --timeout-seconds 5400
```

For environments that require statically assigned guest networking, use:

```bash
proxnix --config ~/.config/proxnix/config exercise lxc \
  --host root@node1 \
  --base-vmid 950 \
  --cleanup-existing \
  --ip 192.168.178.240/24 \
  --gw 192.168.178.1 \
  --nameserver 192.168.178.100
```

The reports land under:

```text
.codex-staging/lxc-exercise/reports/latest/report.md
.codex-staging/lxc-exercise/reports/latest/report.json
.codex-staging/lxc-exercise/reports/latest/artifacts/
```

When the harness fails, read the generated report first and quote the failing
assertions or command logs back to the operator instead of paraphrasing vaguely.

The guest also publishes its own end-to-end status document once the assertion
services run:

```text
http://<guest-ip>:18080/status.json
http://<guest-ip>:18080/
```

Treat that guest status page as the canonical guest-side signal for activation
outputs, service-lifetime secret handling, template rendering, Podman secret
integration, and the final managed config hash. The workstation harness report
is still the main control-plane artifact, but the guest page is the better
source when debugging whether proxnix itself converged correctly.

## Manual canary fallback

Only use this when the operator explicitly does not want the harness or when the
harness is not practical in the current environment.

### 1. Create the workstation-side container directory

```bash
VMID=100
SITE_DIR=~/src/proxnix-site
mkdir -p "$SITE_DIR/containers/$VMID/dropins"
```

Replace `100` and `~/src/proxnix-site` with the approved VMID and the actual
configured site repo path.

### 2. Publish the canary config

For full mode:

```bash
proxnix publish --vmid "$VMID"
```

For config-only mode:

```bash
proxnix publish --config-only --vmid "$VMID"
```

Only use that targeted config-only publish when the canary change does not
depend on newly created or changed secrets or identities.

### 3. Create the canary NixOS CT on the host

Run on the chosen Proxmox node as `root`:

```bash
proxnix-create-lxc --vmid 100 --hostname nixos-canary --yes
```

Use the user-approved VMID and hostname. Override `--template` or `--storage`
only when the operator asked for that or auto-detection is known to be wrong.

### 4. Watch the first boot apply

From the Proxmox host:

```bash
pct exec 100 -- journalctl -u proxnix-apply-config.service -b -f
```

The first boot needs at least 2 GB RAM for Nix evaluation. If it fails, check
memory, network, and DNS before retrying.

### 5. Run health checks

On the Proxmox host:

```bash
proxnix-doctor 100
```

Inside the guest, if deeper inspection is needed:

```bash
pct exec 100 -- systemctl status proxnix-apply-config.service
pct exec 100 -- journalctl -u proxnix-apply-config.service -b
```

### 6. Optional secret-delivery smoke test

Only do this in full mode.

From the workstation:

```bash
proxnix secrets set 100 smoke_test_secret
proxnix publish --vmid 100
```

Then restart the CT:

```bash
pct restart 100
```

Verify inside the guest:

```bash
pct exec 100 -- proxnix-secrets ls
pct exec 100 -- proxnix-secrets get smoke_test_secret
```

If the operator specifically wants to validate guest secret file ownership, add:

```bash
pct exec 100 -- stat -c '%n %u:%g %a %F' /var/lib/proxnix/secrets/identity /var/lib/proxnix/secrets/effective.sops.yaml
```

Expected result:

- both paths are regular files
- owner/group is `0:0`
- mode is `600`

## Success criteria

Treat the deployment as successful only when all of these are true:

- `proxnix-doctor --host-only` passes on every target node
- `proxnix doctor --site-only` passes for the selected publish mode
- `proxnix publish --dry-run --report-changes` shows only expected changes
- `proxnix doctor [--config-only] <host>` passes for the published hosts
- if the exercise harness was approved, the guest `status.json` is green and
  the harness report does not show any real guest-state failures
- if the agent had to use the manual canary fallback instead, `proxnix-doctor <vmid>` passes after first boot

## What not to do

- Do not invent deployment paths outside the Debian package, local
  `host/install.sh`, `host/ansible/install.yml`, or `host/ansible/ai-agent-deploy.yml`
- Do not skip the initial questions and silently choose a secrets model or test
  scope
- Do not treat host install success as enough; always run the verification steps
- Do not skip the exercise harness when the user asked for an end-to-end report
- Do not create a canary CT unless the user explicitly approved that change
- Do not keep live site state in this install repo unless the user explicitly
  asked for that layout
