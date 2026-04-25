{ config, lib, pkgs, ... }:

let
  proxnixStateDir = "/var/lib/proxnix";
  proxnixConfigDir = "${proxnixStateDir}/config";
  proxnixManagedDir = "${proxnixConfigDir}/managed";
  proxnixRuntimeDir = "${proxnixStateDir}/runtime";
  proxnixRuntimeBinDir = "${proxnixRuntimeDir}/bin";
  proxnixRuntimeManifestDir = "${proxnixRuntimeDir}/manifests";
  proxnixSecretDir = "${proxnixStateDir}/secrets";
  proxnixMaterializedSystemdAttachedDir = "/etc/systemd/system.attached";
  proxnixMaterializedSystemdWantsDir = "${proxnixMaterializedSystemdAttachedDir}/multi-user.target.wants";
  proxnixSecretsCommand = "${proxnixRuntimeBinDir}/proxnix-secrets";
  proxnixVmidFile = "${proxnixRuntimeDir}/vmid";
  proxnixCurrentHashFile = "${proxnixRuntimeDir}/current-config-hash";
  proxnixAppliedHashFile = "${proxnixRuntimeDir}/applied-config-hash";
  proxnixHelp = pkgs.writeShellScriptBin "proxnix-help" ''
    set -u

    vmid="$(cat ${proxnixVmidFile} 2>/dev/null || hostname)"
    current="$(cat ${proxnixCurrentHashFile} 2>/dev/null || true)"
    applied="$(cat ${proxnixAppliedHashFile} 2>/dev/null || true)"
    ip_addr="$(ip -4 addr show scope global 2>/dev/null | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }')"
    mem="$(free -h 2>/dev/null | awk '/^Mem:/ { print $3 " / " $2 }')"
    disk="$(df -h / 2>/dev/null | awk 'NR == 2 { print $3 " / " $2 " (" $5 ")" }')"
    fs_type="$(stat -f -c %T / 2>/dev/null || true)"

    printf '\nproxnix help\n'
    printf '============\n\n'
    printf 'Container\n'
    printf '  VMID/host:   %s\n' "$vmid"
    [ -z "$ip_addr" ] || printf '  IP:          %s\n' "$ip_addr"
    [ -z "$mem" ] || printf '  Memory:      %s\n' "$mem"
    [ -z "$disk" ] || printf '  Disk:        %s\n' "$disk"
    printf '  Root FS:     %s\n\n' "''${fs_type:-unknown}"

    printf 'Managed config\n'
    printf '  Files:       /etc/nixos/configuration.nix\n'
    printf '               ${proxnixManagedDir}/{base,common,security-policy,site,proxmox}.nix\n'
    printf '               ${proxnixManagedDir}/dropins/*.nix\n'
    printf '               ${proxnixRuntimeBinDir}/* on PATH\n'
    printf '               proxnix-apply-config.service copied to ${proxnixMaterializedSystemdAttachedDir}/\n'
    printf '               site.nix is optional and usually comes from a separate repo\n'
    printf '  Local hook:  /etc/nixos/local.nix\n'
    if [ -n "$current" ] && [ "$current" != "$applied" ]; then
      printf '  State:       changed; restart the CT or run nixos-rebuild switch\n\n'
    else
      printf '  State:       applied\n\n'
    fi

    printf 'Workloads\n'
    printf '  Guest-owned services and containers live in ${proxnixManagedDir}/dropins/*.nix\n'
    printf '  Writable data  /var/lib/<app>/...\n\n'

    printf 'Useful commands\n'
    printf '  proxnix-help    this screen with live status\n'
    printf '  proxnix-doctor  %s\n' "$vmid"
    printf '  nixos-rebuild   switch\n'
    printf '  podman ps       -a\n'
    printf '  podman logs     -f NAME\n'
    printf '  podman auto-update --dry-run\n'
    printf '  systemctl       status podman-NAME.service\n'
    printf '\n'

    printf 'Secrets\n'
    printf '  proxnix-secrets ls\n'
    printf '  proxnix-secrets get NAME\n'
    printf '  proxnix-secrets set NAME\n'
    printf '  native services read proxnix-managed secrets from their configured paths\n\n'

  '';
in {
  imports = [
    <nixpkgs/nixos/modules/virtualisation/proxmox-lxc.nix>
  ];

  nixpkgs.overlays = [
    (final: prev: {
      unstable = import <nixpkgs-unstable> {
        inherit (prev) config;
        inherit (prev.stdenv.hostPlatform) system;
      };
    })
  ];

  # Shared cross-container operator baseline translated from the legacy
  # Debian/Ansible bootstrap: admin user defaults, journald caps, timesync,
  # swappiness, and a few convenience packages. Forced security posture lives
  # in security-policy.nix so it stays easy to audit.
  proxnix.common.enable = lib.mkDefault true;

  # Networking: let Proxmox own interface addresses and routes.
  # manageNetwork=false enables systemd-networkd so the guest can consume the
  # runtime link config that Proxmox injects at container start.  IP changes
  # in the Proxmox UI take effect on a plain container restart — no NixOS
  # rebuild required.
  proxmoxLXC.manageNetwork = lib.mkDefault false;

  # Nix daemon — no sandbox inside LXC (kernel namespacing not available)
  nix.settings.sandbox = lib.mkDefault false;

  # fstrim is a no-op in LXC and spams the journal
  services.fstrim.enable = lib.mkDefault false;

  # pct enter (lxc-attach) creates an interactive non-login shell, so PAM never
  # runs and /etc/set-environment is never sourced — PATH and NIX_PATH are bare.
  # Sourcing it from /etc/bashrc fixes this for every interactive bash session
  # without affecting normal SSH logins (double-sourcing is harmless).
  programs.bash.interactiveShellInit = lib.mkAfter ''
    [ -f /etc/set-environment ] && . /etc/set-environment
    case ":$PATH:" in *:${proxnixRuntimeBinDir}:*) ;; *) PATH="$PATH:${proxnixRuntimeBinDir}" ;; esac
    case ":$PATH:" in *:/usr/local/sbin:*) ;; *) PATH="$PATH:/usr/local/sbin" ;; esac
    export PATH
  '';

  environment.shellAliases = {
    ll = "ls -alF";
    la = "ls -A";
    l = "ls -CF";
    dps = "podman ps";
    dpsa = "podman ps -a";
  };

  # These mount units don't exist inside an unprivileged LXC container
  systemd.suppressedSystemUnits = [
    "dev-mqueue.mount"
    "sys-kernel-debug.mount"
    "sys-fs-fuse-connections.mount"
  ];

  # Local DNS caching via systemd-resolved.  Clear systemd's built-in public
  # fallback resolvers so a broken Proxmox DNS handoff fails closed instead of
  # silently leaking queries to public DNS.
  services.resolved = {
    enable = lib.mkDefault true;
    dnssec = lib.mkDefault "false";
    extraConfig = ''
      Cache=yes
      FallbackDNS=
    '';
  };

  # ping defaults to cap_net_raw file capability, which requires CAP_SETFCAP to
  # set on the wrapper binary.  CAP_SETFCAP is not available in unprivileged LXC
  # containers, causing suid-sgid-wrappers.service to fail.  Use setuid instead.
  security.wrappers.ping = {
    source = lib.mkForce "${pkgs.iputils}/bin/ping";
    setuid = lib.mkForce true;
    owner = lib.mkForce "root";
    group = lib.mkForce "root";
    capabilities = lib.mkForce "";
  };

  # Weekly Nix store GC: remove generations older than 7 days
  nix.gc = {
    automatic = lib.mkDefault true;
    dates = lib.mkDefault "weekly";
    options = lib.mkDefault "--delete-older-than 7d";
  };

  # sops decrypts the staged YAML secret stores. age still provides the
  # per-container identity used by SOPS.
  environment.systemPackages = [
    pkgs.age
    pkgs.sops
    pkgs.python3Minimal
    proxnixHelp
  ];

  environment.variables = {
    PROXNIX_GUEST_SECRET_DIR = proxnixSecretDir;
  };

  # Ensure the guest-owned proxnix layout exists and keep the copied
  # proxnix-apply-config service enabled after each switch.
  system.activationScripts.proxnix-runtime-setup = lib.stringAfter [ "etc" ] ''
    set -eu

    materialized_systemd_dir="${proxnixMaterializedSystemdAttachedDir}"
    materialized_wants_dir="${proxnixMaterializedSystemdWantsDir}"

    mkdir -p \
      "${proxnixConfigDir}" \
      "${proxnixManagedDir}" \
      "${proxnixRuntimeDir}" \
      "${proxnixRuntimeBinDir}" \
      "${proxnixRuntimeManifestDir}" \
      "${proxnixSecretDir}" \
      /etc/secrets/.ids \
      "$materialized_systemd_dir" \
      "$materialized_wants_dir"
    chmod 700 /etc/secrets /etc/secrets/.ids

    if [ -f "$materialized_systemd_dir/proxnix-apply-config.service" ]; then
      ln -sfn ../proxnix-apply-config.service "$materialized_wants_dir/proxnix-apply-config.service"
    else
      rm -f "$materialized_wants_dir/proxnix-apply-config.service"
    fi
  '';

  # Wire the proxnix helper as the system-wide Podman secret backend.
  # The mount hook pre-registers all proxnix-managed secrets in Podman's
  # metadata (secrets.json) so `podman run --secret name` works immediately
  # after container start without any manual `podman secret create` step.
  environment.etc."containers/containers.conf.d/proxnix-secrets.conf" = {
    mode = "0644";
    text = ''
      [secrets]
      driver = "shell"

      [secrets.opts]
      list   = "${proxnixSecretsCommand} podman list"
      lookup = "${proxnixSecretsCommand} podman lookup"
      store  = "${proxnixSecretsCommand} podman store"
      delete = "${proxnixSecretsCommand} podman delete"
    '';
  };

  # Proxnix does not manage Podman enablement. Container workloads are owned by
  # guest Nix config, typically through site-level imports and per-container
  # dropins.
  virtualisation.podman = {
    enable = lib.mkDefault false;
    dockerCompat = lib.mkDefault false;
    defaultNetwork.settings.dns_enabled = true;
  };

  virtualisation.containers.storage.settings = {
    storage = {
      driver = "overlay";
      graphroot = "/var/lib/containers/storage";
      runroot = "/run/containers/storage";

      options = {
        disable-volatile = true;
        overlay = {
          mountopt = "nodev";
        };
      };
    };
  };

  # State-aware login summary inspired by debian-lxc-container-toolkit's
  # dynamic MOTD, but kept Nix-native and read-only. `proxnix-help` prints the
  # longer command/path reference on demand.
  environment.etc."profile.d/proxnix-login-summary.sh" = {
    mode = "0644";
    text = ''
      case "$-" in
        *i*) ;;
        *) return 0 2>/dev/null || exit 0 ;;
      esac

      [ -z "''${PROXNIX_LOGIN_SUMMARY_SHOWN:-}" ] || return 0 2>/dev/null || exit 0
      export PROXNIX_LOGIN_SUMMARY_SHOWN=1

      _ip="$(ip -4 addr show scope global 2>/dev/null | awk '/inet / { sub(/\/.*/, "", $2); print $2; exit }')"
      _mem="$(free -h 2>/dev/null | awk '/^Mem:/ { print $3 " / " $2 }')"
      _disk="$(df -h / 2>/dev/null | awk 'NR == 2 { print $3 " / " $2 " (" $5 ")" }')"
      _podman_state="not enabled"
      _podman_containers=""

      if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
        _podman_state="ready"
        _podman_count="$(podman ps --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')"
        _podman_names="$(podman ps --format '{{.Names}}' 2>/dev/null | paste -sd ', ' -)"
        _podman_containers="''${_podman_count:-0} running"
        [ -z "$_podman_names" ] || _podman_containers="$_podman_containers: $_podman_names"
      elif command -v podman >/dev/null 2>&1; then
        _podman_state="installed, not responding"
      fi

      printf '\n  proxnix status\n'
      [ -z "$_ip" ] || printf '    IP:       %s\n' "$_ip"
      [ -z "$_mem" ] || printf '    Memory:   %s\n' "$_mem"
      [ -z "$_disk" ] || printf '    Disk:     %s\n' "$_disk"
      printf '    Podman:   %s\n' "$_podman_state"
      [ -z "$_podman_containers" ] || printf '    Running:  %s\n' "$_podman_containers"

      printf '    Commands: proxnix-help | proxnix-doctor %s | podman ps -a\n\n' \
        "$(cat ${proxnixVmidFile} 2>/dev/null || hostname)"

      unset _ip _mem _disk _podman_state _podman_count _podman_names _podman_containers
    '';
  };

  # ── Config-drift reminder ─────────────────────────────────────────────────
  # Shown at every login when the managed config pushed by the pre-start hook
  # differs from the last applied generation, so the operator knows the next
  # container restart will auto-apply it or that they can rebuild manually.
  environment.etc."profile.d/proxnix-rebuild-hint.sh" = {
    mode = "0644";
    text = ''
      _current="$(cat ${proxnixCurrentHashFile} 2>/dev/null || true)"
      _applied="$(cat ${proxnixAppliedHashFile} 2>/dev/null || true)"
      if [ -n "$_current" ] && [ "$_current" != "$_applied" ]; then
        printf '\n  proxnix: managed config has changed — restart the container to auto-apply,\n'
        printf '           or run manually:\n'
        printf '    nixos-rebuild switch\n\n'
      fi
      unset _current _applied
    '';
  };

  # ── Message of the day ────────────────────────────────────────────────────
  users.motd = ''

    proxnix
    ========

    Managed config
      /etc/nixos/configuration.nix
      ${proxnixManagedDir}/{base,common,security-policy,site,proxmox}.nix
      ${proxnixManagedDir}/dropins/*.nix
      ${proxnixRuntimeBinDir}/* on PATH
      proxnix-apply-config.service  copied to ${proxnixMaterializedSystemdAttachedDir}/
      ${proxnixManagedDir}/site.nix  optional site override
      /etc/nixos/local.nix  optional local override

    Workloads
      ${proxnixManagedDir}/dropins/*.nix  guest-owned services and containers

    Secrets
      proxnix-secrets ls
      proxnix-secrets get NAME
      proxnix-secrets set NAME

    Live state
      proxnix-help               full status and commands
      proxnix-doctor VMID        host-side health check
      nixos-rebuild switch       apply managed config

    Containers
      podman ps -a
      podman logs -f NAME
      podman auto-update --dry-run
      systemctl status podman-NAME.service

    Maintenance
      nix-collect-garbage -d
      nixos-rebuild list-generations
  '';
}
