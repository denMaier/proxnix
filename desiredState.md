# Workstation

## 'proxnix publish' 
  * syncs workstation source of truth into ALL hosts, no reconciliation
  * '--reconcile' -> reconciles all running containers
  * '--vmid' -> publishes only that vmid
  * '--reconcile --vmid' -> publishes and reconciles only that vmid

## 'proxnix reconcile'
  * reconciles only
  * has same vmid gating as publish

# Host

All checks that we discussed are done at the appropriate steps.
Decision: status JSON stays the operator-facing compatibility/status surface,
SQLite is the durable orchestration memory for history, locks/leases, retries,
and GC decisions.
Build reuse should be optimized locally on each host by keeping a
golden-template build warm, so normal container builds reuse most store paths
without needing cross-node closure transfer machinery.
Site flake.lock is optional. If committed and published, it is copied into the
generated authority flake so golden and CT builds share the same pinned nixpkgs
revision. If absent, host builds use rolling mode without writing a local lock.

## current command names
  * 'proxnix-reconcile-build-golden'
  * 'proxnix-reconcile-build'
  * 'proxnix-reconcile-seed'
  * 'proxnix-reconcile-seed-offline'
  * 'proxnix-reconcile-activate'
  * 'proxnix-reconcile' = build + seed + activate for a running container

## 'proxnix-reconcile-build-golden'
  * builds and GC-protects a host-local golden-template NixOS system
  * used only to warm the local host Nix store so CT builds reuse common paths
## 'proxnix-reconcile-build'
  * renders a config set from staged configs and builds a closure and sets is "not yet seeded, not yet activated"
  * reuses the host's local Nix store, especially the local golden-template build
  * checks pre-build if build is neccessary
## 'proxnix-reconcile-seed'
  * seeds a closure into a running container
  * dispatches to offline seed only when a stopped container rootfs is mounted/provided
  * fails clearly for a stopped container without a mounted/provided rootfs
## 'proxnix-reconcile-seed-offline'
  * same as seed but for offline containers
## 'proxnix-reconcile-activate'
  * helper to activate / switch the container to the new closure

## lxc pre-start hook
  * opportunistically runs a proxnix-reconcile-build-golden for local build reuse
  * runs a proxnix-reconcile-build for the container
## lxc mount hook 
  * runs a proxnix-reconcile-seed-offline for the container
  * rsync-copies a non-authoritative build-input snapshot into the container at /var/lib/proxnix/build-input/
  * does not install /etc/nixos/configuration.nix or make guest config authoritative

## Systemd units
  ### Garbage collection
  * uses sqlite db and other signals to remove the locks dir and garbage collect local nix store
  ### Reconciliation
  * no full reconcile timer; reconcile is triggered by LXC lifecycle hooks or explicit operator/workstation commands

# LXC
  * Has a local copy of the config used to create its state
  * Is set up/reconcilliated at boot time and can be forced to be reconcilliated by the host at runtime
  * Has defined systemd secret services and the secret store staged 


# Deployment
  # Hosts 
    * nix must be installed and flakes activated 
    * then deploy as flake?!
    * otherwise Ansible playbook deployment 
    * drop completely deb-package and local install script, keep uninstall script
    
  # Workstation
    * CLI using pypi or nix or flake
    * Electrobun app using homebrew or nix or flake
