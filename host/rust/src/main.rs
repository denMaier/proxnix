use rusqlite::{params, Connection};
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap};
use std::env;
use std::error::Error;
use std::fs;
use std::io::{self, Write};
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{self, Command, Stdio};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use uuid::Uuid;

mod flake_update;
mod gc;
mod reconcile_phase;

type HostResult<T> = Result<T, Box<dyn Error>>;

fn main() {
    if let Err(err) = run(env::args().collect()) {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}

fn run(args: Vec<String>) -> Result<(), String> {
    match args.get(1).map(String::as_str) {
        Some("pve-conf-to-nix") => pve_conf_to_nix_main(&args[2..]),
        Some("authority") => authority_main(&args[2..]),
        Some("flake-update") => flake_update::main(&args[2..]),
        Some("gc") => gc::main(&args[2..]),
        Some("hook") => hook_main(&args[2..]),
        Some("reconcile") => reconcile_main(&args[2..]),
        Some("state") => state_main(&args[2..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some("-V") | Some("--version") => {
            println!("proxnix-host {}", version());
            Ok(())
        }
        Some(command) => Err(format!("unknown subcommand: {command}")),
    }
}

fn version() -> &'static str {
    option_env!("PROXNIX_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
}

fn print_usage() {
    println!(
        "\
Usage:
  proxnix-host pve-conf-to-nix --pve-conf <path> --out-dir <dir>
  proxnix-host authority render --root <dir> --authority <dir> --pve-lxc-dir <dir> --node-name <name>
  proxnix-host flake-update [--force] [--frequency daily|weekly|monthly|disabled] [--input <name>] [input ...]
  proxnix-host gc [--dry-run]
  proxnix-host hook prestart [--vmid <vmid>] [--pve-conf <path>]
  proxnix-host hook mount [--vmid <vmid>] [--rootfs <path>]
  proxnix-host hook poststop [--vmid <vmid>]
  proxnix-host reconcile build-golden [--node-name <name>]
  proxnix-host reconcile podman-secrets --rootfs <path> --vmid <vmid> --secrets-dir <dir>
  proxnix-host reconcile seed-offline --vmid <vmid> --rootfs <path>
  proxnix-host state [--db <path>] <init|observe-container|observe-closure|record-attempt>
  proxnix-host --version
"
    );
}

fn authority_main(args: &[String]) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("render") => authority_render_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(format!("unknown authority subcommand: {command}")),
    }
}

fn reconcile_main(args: &[String]) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("build-golden") => reconcile_phase::build_golden_main(&args[1..]),
        Some("podman-secrets") => reconcile_podman_secrets_main(&args[1..]),
        Some("seed-offline") => reconcile_phase::seed_offline_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(format!("unknown reconcile subcommand: {command}")),
    }
}

fn hook_main(args: &[String]) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("prestart") => hook_prestart_main(&args[1..]),
        Some("mount") => hook_mount_main(&args[1..]),
        Some("poststop") => hook_poststop_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(format!("unknown hook subcommand: {command}")),
    }
}

#[derive(Debug, Clone)]
struct HookPaths {
    proxnix_dir: PathBuf,
    proxnix_priv_dir: PathBuf,
    host_state_dir: PathBuf,
    run_dir: PathBuf,
    secrets_guest_bin: PathBuf,
}

impl HookPaths {
    fn from_env() -> Self {
        let proxnix_dir = env_path("PROXNIX_DIR", "/var/lib/proxnix");
        let proxnix_priv_dir = env::var_os("PROXNIX_PRIV_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| proxnix_dir.join("private"));
        let host_state_dir = env_path("PROXNIX_HOST_STATE_DIR", "/etc/proxnix");
        let run_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
        let lib_dir = env_path("PROXNIX_LIB_DIR", "/usr/local/lib/proxnix");
        let secrets_guest_bin = env::var_os("PROXNIX_SECRETS_GUEST_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| lib_dir.join("proxnix-secrets-guest"));

        Self {
            proxnix_dir,
            proxnix_priv_dir,
            host_state_dir,
            run_dir,
            secrets_guest_bin,
        }
    }

    fn container_dir(&self, vmid: &str) -> PathBuf {
        self.proxnix_dir.join("containers").join(vmid)
    }

    fn container_priv_dir(&self, vmid: &str) -> PathBuf {
        self.proxnix_priv_dir.join("containers").join(vmid)
    }

    fn stage_dir(&self, vmid: &str) -> io::Result<PathBuf> {
        fs::create_dir_all(&self.run_dir)?;
        set_mode(&self.run_dir, 0o711)?;
        Ok(self.run_dir.join(vmid))
    }
}

fn env_path(name: &str, default: &str) -> PathBuf {
    env::var_os(name)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(default))
}

fn hook_prestart_main(args: &[String]) -> Result<(), String> {
    let start = Instant::now();
    let parsed = parse_prestart_args(args)?;
    if parsed.help {
        print_prestart_usage();
        return Ok(());
    }

    let vmid = parsed
        .vmid
        .or_else(|| env::var("LXC_NAME").ok())
        .ok_or_else(|| "VMID not set (pass --vmid or export LXC_NAME).".to_owned())?;
    if !valid_vmid(&vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    let mut stage_complete = false;
    let paths = HookPaths::from_env();
    let stage_dir = paths
        .stage_dir(&vmid)
        .map_err(|err| format!("failed to prepare stage base: {err}"))?;

    let result = hook_prestart_run(
        &paths,
        &vmid,
        parsed.pve_conf,
        &stage_dir,
        &mut stage_complete,
    );
    let elapsed = start.elapsed().as_secs();
    match result {
        Ok(()) => {
            hook_log(
                "proxnix-prestart",
                "prestart",
                &vmid,
                &format!("Finished pre-start hook in {elapsed}s."),
            );
            Ok(())
        }
        Err(err) => {
            if !stage_complete {
                hook_log(
                    "proxnix-prestart",
                    "prestart",
                    &vmid,
                    &format!(
                        "ERROR: pre-start hook did not complete cleanly after {elapsed}s; removing incomplete stage {}.",
                        stage_dir.display()
                    ),
                );
                let _ = remove_path_if_exists(&stage_dir);
            }
            Err(err)
        }
    }
}

fn hook_prestart_run(
    paths: &HookPaths,
    vmid: &str,
    pve_conf_override: Option<PathBuf>,
    stage_dir: &Path,
    stage_complete: &mut bool,
) -> Result<(), String> {
    if !valid_vmid(vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }

    let pve_conf =
        pve_conf_override.unwrap_or_else(|| PathBuf::from(format!("/etc/pve/lxc/{vmid}.conf")));
    let container_template_dir = paths.proxnix_dir.join("containers/_template");
    let container_dir = paths.container_dir(vmid);
    let container_priv_dir = paths.container_priv_dir(vmid);
    let template_selector_dir = container_dir.join("templates");
    let dropin_dir = container_dir.join("dropins");
    let bind_stage_dir = stage_dir.join("bind");
    let bind_config_dir = bind_stage_dir.join("config");
    let managed_dir = bind_config_dir.join("managed");
    let managed_dropin_dir = managed_dir.join("dropins");
    let managed_template_dir = managed_dir.join("_template");
    let bind_runtime_dir = bind_stage_dir.join("runtime");
    let secrets_stage_dir = bind_stage_dir.join("secrets");
    let copy_runtime_bin_dir = stage_dir.join("copy/runtime/bin");

    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!(
            "Starting pre-start hook (PVE conf: {}, stage: {}).",
            pve_conf.display(),
            stage_dir.display()
        ),
    );

    for required in [
        "configuration.nix",
        "base.nix",
        "common.nix",
        "security-policy.nix",
    ] {
        let source = paths.proxnix_dir.join(required);
        if !source.is_file() {
            hook_log(
                "proxnix-prestart",
                "prestart",
                vmid,
                &format!("ERROR: missing required shared file: {}", source.display()),
            );
            return Err("required shared file missing; rerun host install".to_owned());
        }
    }

    if !paths.secrets_guest_bin.is_file() {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            &format!("ERROR: {} not found.", paths.secrets_guest_bin.display()),
        );
        return Err("proxnix-secrets-guest missing; rerun host install".to_owned());
    }

    if !command_exists("sops") {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "ERROR: sops not found on the Proxmox host.",
        );
        return Err("sops not found on the Proxmox host".to_owned());
    }

    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        "Preparing fresh stage directory.",
    );
    remove_path_if_exists(stage_dir).map_err(|err| format!("failed to clean stage: {err}"))?;
    for dir in [
        managed_dropin_dir.as_path(),
        bind_runtime_dir.as_path(),
        secrets_stage_dir.as_path(),
        copy_runtime_bin_dir.as_path(),
    ] {
        fs::create_dir_all(dir)
            .map_err(|err| format!("failed to create {}: {err}", dir.display()))?;
    }

    copy_file(
        &paths.proxnix_dir.join("configuration.nix"),
        &bind_config_dir.join("configuration.nix"),
        0o600,
    )?;
    copy_file(
        &paths.proxnix_dir.join("base.nix"),
        &managed_dir.join("base.nix"),
        0o600,
    )?;
    copy_file(
        &paths.proxnix_dir.join("common.nix"),
        &managed_dir.join("common.nix"),
        0o600,
    )?;
    copy_file(
        &paths.proxnix_dir.join("security-policy.nix"),
        &managed_dir.join("security-policy.nix"),
        0o600,
    )?;
    if paths.proxnix_dir.join("site.nix").is_file() {
        copy_file(
            &paths.proxnix_dir.join("site.nix"),
            &managed_dir.join("site.nix"),
            0o600,
        )?;
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Included optional site.nix.",
        );
    } else {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "No optional site.nix present.",
        );
    }

    for legacy_yaml in [
        container_dir.join("proxmox.yaml"),
        container_dir.join("user.yaml"),
    ] {
        if legacy_yaml.is_file() {
            hook_log(
                "proxnix-prestart",
                "prestart",
                vmid,
                &format!(
                    "ERROR: legacy YAML config is no longer supported: {}",
                    legacy_yaml.display()
                ),
            );
            return Err("legacy YAML config is no longer supported".to_owned());
        }
    }

    let rendered = generate_proxmox_nix(
        &parse_pve_conf(&pve_conf)
            .map_err(|err| format!("failed to read {}: {err}", pve_conf.display()))?,
    );
    write_text_file(&managed_dir.join("proxmox.nix"), &rendered, 0o600)
        .map_err(|err| format!("failed to render Proxmox CT config: {err}"))?;
    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        "Rendered Proxmox CT config.",
    );

    if dir_has_entries(&container_dir.join("quadlets"))? {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            &format!(
                "ERROR: raw quadlet passthrough is no longer supported: {}",
                container_dir.join("quadlets").display()
            ),
        );
        return Err("raw quadlet passthrough is no longer supported".to_owned());
    }

    stage_templates(
        vmid,
        &template_selector_dir,
        &container_template_dir,
        &managed_template_dir,
    )?;
    stage_dropins(
        vmid,
        &dropin_dir,
        &managed_dropin_dir,
        &copy_runtime_bin_dir,
    )?;

    let desired_config_hash = hash_tree(&bind_config_dir)
        .map_err(|err| format!("failed to hash staged config tree: {err}"))?;
    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!("Computed desired config hash: {desired_config_hash}."),
    );

    let effective_secrets = container_priv_dir.join("effective.sops.yaml");
    if effective_secrets.is_file() {
        copy_file(
            &effective_secrets,
            &secrets_stage_dir.join("effective.sops.yaml"),
            0o600,
        )?;
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Staged effective encrypted secrets store.",
        );
    } else {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "No effective encrypted secrets store found.",
        );
    }

    write_text_file(
        &bind_runtime_dir.join("current-config-hash"),
        &desired_config_hash,
        0o600,
    )
    .map_err(|err| format!("failed to write runtime hash marker: {err}"))?;
    write_text_file(&bind_runtime_dir.join("vmid"), vmid, 0o600)
        .map_err(|err| format!("failed to write runtime VMID marker: {err}"))?;
    copy_file(
        &paths.secrets_guest_bin,
        &copy_runtime_bin_dir.join("proxnix-secrets"),
        0o700,
    )?;

    let identity_store = container_priv_dir.join("age_identity.sops.yaml");
    if identity_store.is_file() {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Decrypting host-relay container identity for mount-time staging.",
        );
        decrypt_host_identity_store_to_file(
            &identity_store,
            &secrets_stage_dir.join("identity"),
            &paths.host_state_dir.join("host_relay_identity"),
        )
        .map_err(|err| format!("failed to decrypt host-relay container identity: {err}"))?;
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Staged decrypted container identity.",
        );
    } else {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "No host-relay container identity store found.",
        );
    }

    let host_subuid = determine_host_root_uid(&pve_conf)
        .map_err(|err| format!("could not determine container host root UID: {err}"))?;
    restrict_stage_tree(vmid, stage_dir, &copy_runtime_bin_dir, &host_subuid)?;

    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!(
            "Rendered staged state at {} (host root uid: {host_subuid})",
            stage_dir.display()
        ),
    );
    *stage_complete = true;
    run_prestart_build(vmid);
    Ok(())
}

fn hook_mount_main(args: &[String]) -> Result<(), String> {
    let start = Instant::now();
    let parsed = parse_mount_args(args)?;
    if parsed.help {
        print_mount_usage();
        return Ok(());
    }
    let vmid = parsed
        .vmid
        .or_else(|| env::var("LXC_NAME").ok())
        .ok_or_else(|| "VMID not set (pass --vmid or export LXC_NAME).".to_owned())?;
    let rootfs = parsed
        .rootfs
        .or_else(|| env::var_os("LXC_ROOTFS_MOUNT").map(PathBuf::from))
        .ok_or_else(|| {
            "rootfs mount not set (pass --rootfs or export LXC_ROOTFS_MOUNT).".to_owned()
        })?;
    let result = hook_mount_run(&HookPaths::from_env(), &vmid, &rootfs);
    let elapsed = start.elapsed().as_secs();
    if result.is_ok() {
        hook_log(
            "proxnix-mount",
            "mount",
            &vmid,
            &format!("Finished mount hook in {elapsed}s."),
        );
    } else {
        hook_log(
            "proxnix-mount",
            "mount",
            &vmid,
            &format!("ERROR: mount hook failed after {elapsed}s."),
        );
    }
    result
}

fn hook_mount_run(paths: &HookPaths, vmid: &str, rootfs: &Path) -> Result<(), String> {
    if !valid_vmid(vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    let stage_dir = paths
        .stage_dir(vmid)
        .map_err(|err| format!("failed to resolve stage directory: {err}"))?;
    let bind_stage_dir = stage_dir.join("bind");
    let bind_config_dir = bind_stage_dir.join("config");
    let bind_runtime_dir = bind_stage_dir.join("runtime");
    let bind_secrets_dir = bind_stage_dir.join("secrets");
    let bind_quadlet_dir = bind_stage_dir.join("quadlet");
    let copy_runtime_bin_dir = stage_dir.join("copy/runtime/bin");
    let desired_config_hash = fs::read_to_string(bind_runtime_dir.join("current-config-hash"))
        .unwrap_or_default()
        .replace(['\r', '\n'], "");

    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        &format!(
            "Starting mount hook (rootfs: {}, stage: {}).",
            rootfs.display(),
            stage_dir.display()
        ),
    );

    if !rootfs.is_dir() {
        return Err("rootfs mount not accessible".to_owned());
    }
    if !rootfs.join("etc").is_dir() {
        return Err(format!(
            "{} missing; rootfs does not look right",
            rootfs.join("etc").display()
        ));
    }
    if !stage_dir.is_dir() {
        return Err(format!(
            "staged proxnix state missing at {}",
            stage_dir.display()
        ));
    }
    if !bind_config_dir.is_dir()
        || !copy_runtime_bin_dir.is_dir()
        || !bind_runtime_dir.join("vmid").is_file()
        || !bind_runtime_dir.join("current-config-hash").is_file()
        || desired_config_hash.is_empty()
    {
        return Err(format!(
            "staged proxnix state is incomplete under {}",
            stage_dir.display()
        ));
    }
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        &format!("Validated staged proxnix state (desired hash {desired_config_hash})."),
    );

    let proxnix_state_dir = rootfs.join("var/lib/proxnix");
    let build_input_dir = proxnix_state_dir.join("build-input");
    let runtime_dir = proxnix_state_dir.join("runtime");
    let runtime_bin_dir = runtime_dir.join("bin");
    let manifest_dir = runtime_dir.join("manifests");
    let runtime_quadlet_dir = runtime_dir.join("quadlets");
    let secret_dir = proxnix_state_dir.join("secrets");
    let systemd_attached_dir = rootfs.join("etc/systemd/system.attached");
    let systemd_wants_dir = systemd_attached_dir.join("multi-user.target.wants");
    let runtime_current_hash = runtime_dir.join("current-config-hash");
    let runtime_vmid = runtime_dir.join("vmid");

    remove_legacy_real_systemd_dir(vmid, rootfs)?;

    for dir in [
        runtime_dir.as_path(),
        runtime_bin_dir.as_path(),
        manifest_dir.as_path(),
        runtime_quadlet_dir.as_path(),
        secret_dir.as_path(),
        rootfs.join("etc/secrets").as_path(),
        systemd_attached_dir.as_path(),
        systemd_wants_dir.as_path(),
    ] {
        fs::create_dir_all(dir)
            .map_err(|err| format!("failed to create {}: {err}", dir.display()))?;
    }
    set_mode(&rootfs.join("etc/secrets"), 0o700)
        .map_err(|err| format!("failed to chmod /etc/secrets: {err}"))?;
    set_mode(&secret_dir, 0o700).map_err(|err| format!("failed to chmod secrets dir: {err}"))?;

    if command_exists("proxnix-reconcile-seed-offline") {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "Seeding desired NixOS closure into stopped rootfs when needed.",
        );
        let status = Command::new("proxnix-reconcile-seed-offline")
            .arg("--vmid")
            .arg(vmid)
            .arg("--rootfs")
            .arg(rootfs)
            .status()
            .map_err(|err| format!("failed to run proxnix-reconcile-seed-offline: {err}"))?;
        if !status.success() {
            return Err("proxnix-reconcile-seed-offline failed".to_owned());
        }
    } else {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "WARN: proxnix-reconcile-seed-offline is unavailable; skipping offline closure seed.",
        );
    }

    remove_guest_path(&proxnix_state_dir.join("config"))?;
    sync_build_input_snapshot(vmid, &bind_config_dir, &build_input_dir);
    remove_file_if_exists(&rootfs.join("etc/nixos/configuration.nix"))
        .map_err(|err| format!("failed to remove legacy configuration.nix: {err}"))?;
    remove_guest_path(&rootfs.join("etc/nixos/managed"))?;
    for path in [
        rootfs.join("etc/nixos/base.nix"),
        rootfs.join("etc/nixos/common.nix"),
        rootfs.join("etc/nixos/proxmox.nix"),
        rootfs.join("etc/nixos/local.nix"),
    ] {
        remove_file_if_exists(&path)
            .map_err(|err| format!("failed to remove {}: {err}", path.display()))?;
    }
    remove_path_if_exists(&rootfs.join("etc/nixos/dropins"))
        .map_err(|err| format!("failed to remove legacy dropins: {err}"))?;

    let current_config_hash = fs::read_to_string(&runtime_current_hash)
        .unwrap_or_default()
        .replace(['\r', '\n'], "");
    if desired_config_hash != current_config_hash {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            &format!("Updated diagnostic managed config hash ({desired_config_hash})"),
        );
    } else {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            &format!("Diagnostic managed config hash unchanged ({desired_config_hash})"),
        );
    }

    bind_ro_file(
        &bind_runtime_dir.join("current-config-hash"),
        &runtime_current_hash,
    )?;
    bind_ro_file(&bind_runtime_dir.join("vmid"), &runtime_vmid)?;
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        "Bound runtime markers read-only.",
    );

    remove_guest_path(&runtime_bin_dir)?;
    fs::create_dir_all(&runtime_bin_dir)
        .map_err(|err| format!("failed to create runtime bin dir: {err}"))?;
    sync_copied_guest_file_manifest(
        &copy_runtime_bin_dir,
        &runtime_bin_dir,
        &manifest_dir.join("managed-runtime-bin.list"),
        0o555,
        |_| true,
    )?;
    for path in [
        runtime_dir.join("proxnix-apply-config-runner"),
        systemd_attached_dir.join("proxnix-apply-config.service"),
        systemd_wants_dir.join("proxnix-apply-config.service"),
    ] {
        remove_guest_path(&path)?;
    }
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        "Installed guest runtime helpers and removed legacy proxnix-apply-config service.",
    );

    for path in legacy_apply_config_paths(rootfs) {
        remove_file_if_exists(&path)
            .map_err(|err| format!("failed to remove {}: {err}", path.display()))?;
    }

    sync_bound_entry_manifest(
        &bind_quadlet_dir,
        &runtime_quadlet_dir,
        &manifest_dir.join("managed-quadlets.list"),
    )?;
    sync_bound_file_manifest(
        &bind_quadlet_dir,
        &rootfs.join("etc/containers/systemd"),
        &manifest_dir.join("managed-quadlet-units.list"),
        is_quadlet_unit_file,
    )?;
    sync_copied_guest_file_manifest(
        &bind_secrets_dir,
        &secret_dir,
        &manifest_dir.join("managed-secrets.list"),
        0o600,
        |_| true,
    )?;
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        "Synced managed quadlet and secret manifests.",
    );

    let _ = fs::remove_dir(&runtime_quadlet_dir);
    let _ = fs::remove_dir(rootfs.join("etc/containers/systemd"));

    for path in [
        rootfs.join("etc/proxnix/current-config-hash"),
        rootfs.join("etc/proxnix/applied-config-hash"),
        rootfs.join("etc/proxnix/vmid"),
        rootfs.join("etc/proxnix/proxnix-apply-config-runner"),
        rootfs.join("etc/proxnix/secrets"),
        rootfs.join("etc/proxnix/quadlets"),
    ] {
        remove_guest_path(&path)?;
    }
    for path in [
        rootfs.join("etc/proxnix/managed-quadlets.list"),
        rootfs.join("etc/proxnix/managed-quadlet-units.list"),
        rootfs.join("etc/containers/containers.conf.d/age-secrets.conf"),
    ] {
        remove_file_if_exists(&path)
            .map_err(|err| format!("failed to remove {}: {err}", path.display()))?;
    }
    let _ = fs::remove_dir(rootfs.join("etc/proxnix"));
    delete_top_level_matching(&rootfs.join("etc/secrets"), |path| {
        path.extension().and_then(|ext| ext.to_str()) == Some("age")
    })?;

    reconcile_podman_secrets(rootfs, vmid, &secret_dir)
        .map_err(|err| format!("failed to reconcile Podman secrets: {err}"))?;
    remove_file_if_exists(&rootfs.join("root/proxnix-bootstrap.sh"))
        .map_err(|err| format!("failed to remove legacy bootstrap helper: {err}"))?;
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        "Removed legacy guest-side bootstrap helper.",
    );
    hook_log(
        "proxnix-mount",
        "mount",
        vmid,
        "Mount-time bind setup complete.",
    );
    Ok(())
}

fn hook_poststop_main(args: &[String]) -> Result<(), String> {
    let parsed = parse_poststop_args(args)?;
    if parsed.help {
        print_poststop_usage();
        return Ok(());
    }
    let Some(vmid) = parsed.vmid.or_else(|| env::var("LXC_NAME").ok()) else {
        return Ok(());
    };
    if !valid_vmid(&vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    let paths = HookPaths::from_env();
    let stage_dir = paths
        .stage_dir(&vmid)
        .map_err(|err| format!("failed to resolve stage directory: {err}"))?;
    if stage_dir.is_dir() {
        remove_path_if_exists(&stage_dir)
            .map_err(|err| format!("failed to remove stage: {err}"))?;
        hook_log(
            "proxnix-poststop",
            "poststop",
            &vmid,
            &format!("Removed staged state at {}.", stage_dir.display()),
        );
    } else {
        hook_log(
            "proxnix-poststop",
            "poststop",
            &vmid,
            &format!("No staged state to remove at {}.", stage_dir.display()),
        );
    }
    Ok(())
}

#[derive(Debug, Default, PartialEq, Eq)]
struct ParsedPrestartArgs {
    vmid: Option<String>,
    pve_conf: Option<PathBuf>,
    help: bool,
}

#[derive(Debug, Default, PartialEq, Eq)]
struct ParsedMountArgs {
    vmid: Option<String>,
    rootfs: Option<PathBuf>,
    help: bool,
}

#[derive(Debug, Default, PartialEq, Eq)]
struct ParsedPoststopArgs {
    vmid: Option<String>,
    help: bool,
}

fn parse_prestart_args(args: &[String]) -> Result<ParsedPrestartArgs, String> {
    let mut parsed = ParsedPrestartArgs::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => parsed.vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--pve-conf" => {
                parsed.pve_conf = Some(PathBuf::from(take_arg(args, &mut index, "--pve-conf")?))
            }
            "--help" | "-h" => parsed.help = true,
            "lxc" | "pre-start" => {}
            other if parsed.vmid.is_none() => parsed.vmid = Some(other.to_owned()),
            other => return Err(format!("unknown prestart hook argument: {other}")),
        }
        index += 1;
    }
    Ok(parsed)
}

fn parse_mount_args(args: &[String]) -> Result<ParsedMountArgs, String> {
    let mut parsed = ParsedMountArgs::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => parsed.vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--rootfs" => {
                parsed.rootfs = Some(PathBuf::from(take_arg(args, &mut index, "--rootfs")?))
            }
            "--help" | "-h" => parsed.help = true,
            "lxc" | "mount" => {}
            other if parsed.vmid.is_none() => parsed.vmid = Some(other.to_owned()),
            other => return Err(format!("unknown mount hook argument: {other}")),
        }
        index += 1;
    }
    Ok(parsed)
}

fn parse_poststop_args(args: &[String]) -> Result<ParsedPoststopArgs, String> {
    let mut parsed = ParsedPoststopArgs::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => parsed.vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--help" | "-h" => parsed.help = true,
            "lxc" | "post-stop" => {}
            other if parsed.vmid.is_none() => parsed.vmid = Some(other.to_owned()),
            other => return Err(format!("unknown poststop hook argument: {other}")),
        }
        index += 1;
    }
    Ok(parsed)
}

fn print_prestart_usage() {
    println!(
        "\
Usage:
  proxnix-host hook prestart
  proxnix-host hook prestart <vmid> lxc pre-start
  proxnix-host hook prestart --vmid <vmid> [--pve-conf </path/to/lxc.conf>]
"
    );
}

fn print_mount_usage() {
    println!(
        "\
Usage:
  proxnix-host hook mount
  proxnix-host hook mount <vmid> lxc mount
  proxnix-host hook mount --vmid <vmid> --rootfs </path/to/rootfs>
"
    );
}

fn print_poststop_usage() {
    println!(
        "\
Usage:
  proxnix-host hook poststop
  proxnix-host hook poststop <vmid> lxc post-stop
  proxnix-host hook poststop --vmid <vmid>
"
    );
}

fn valid_vmid(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
}

fn hook_log(logger_tag: &str, prefix: &str, vmid: &str, message: &str) {
    let line = format!(
        "[proxnix-{prefix}][{}] {message}",
        if vmid.is_empty() { "unknown" } else { vmid }
    );
    eprintln!("{line}");
    if command_exists("logger") {
        let _ = Command::new("logger")
            .arg("-t")
            .arg(logger_tag)
            .arg("--")
            .arg(&line)
            .status();
    }
}

fn command_exists(command: &str) -> bool {
    find_in_path(command).is_some()
}

fn copy_file(source: &Path, dest: &Path, mode: u32) -> Result<(), String> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .map_err(|err| format!("failed to create {}: {err}", parent.display()))?;
    }
    fs::copy(source, dest).map_err(|err| {
        format!(
            "failed to copy {} to {}: {err}",
            source.display(),
            dest.display()
        )
    })?;
    set_mode(dest, mode).map_err(|err| format!("failed to chmod {}: {err}", dest.display()))
}

fn write_text_file(path: &Path, content: &str, mode: u32) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)?;
    set_mode(path, mode)
}

fn dir_has_entries(path: &Path) -> Result<bool, String> {
    if !path.is_dir() {
        return Ok(false);
    }
    Ok(fs::read_dir(path)
        .map_err(|err| format!("failed to read {}: {err}", path.display()))?
        .next()
        .is_some())
}

fn stage_templates(
    vmid: &str,
    selector_dir: &Path,
    template_dir: &Path,
    managed_template_dir: &Path,
) -> Result<(), String> {
    if !selector_dir.is_dir() {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "No container template selectors found.",
        );
        return Ok(());
    }
    if !template_dir.is_dir() {
        return Err(format!(
            "{} exists but {} is missing",
            selector_dir.display(),
            template_dir.display()
        ));
    }

    let mut count = 0;
    for selector in sorted_dir_entries(selector_dir)? {
        let selector_path = selector.path();
        if !selector_path.is_file()
            || selector_path.extension().and_then(|ext| ext.to_str()) != Some("template")
        {
            continue;
        }
        let selector_name = selector.file_name().to_string_lossy().into_owned();
        let template_name = selector_name.trim_end_matches(".template");
        let source = template_dir.join(template_name);
        if !source.is_dir() {
            return Err(format!("selected template not found: {}", source.display()));
        }
        fs::create_dir_all(managed_template_dir)
            .map_err(|err| format!("failed to create {}: {err}", managed_template_dir.display()))?;
        let dest = managed_template_dir.join(template_name);
        remove_path_if_exists(&dest).map_err(|err| format!("failed to replace template: {err}"))?;
        copy_dir_recursive(&source, &dest)?;
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            &format!("Included template: {template_name}"),
        );
        count += 1;
    }
    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!("Processed {count} selected template(s)."),
    );
    Ok(())
}

fn stage_dropins(
    vmid: &str,
    dropin_dir: &Path,
    managed_dropin_dir: &Path,
    copy_runtime_bin_dir: &Path,
) -> Result<(), String> {
    if !dropin_dir.is_dir() {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "No container drop-ins found.",
        );
        return Ok(());
    }

    let mut nix_dropins = 0;
    let mut script_dropins = 0;
    let mut dir_dropins = 0;
    for entry in sorted_dir_entries(dropin_dir)? {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if path.is_file() {
            match path.extension().and_then(|ext| ext.to_str()) {
                Some("nix") => {
                    copy_file(&path, &managed_dropin_dir.join(&name), 0o600)?;
                    nix_dropins += 1;
                }
                Some("service") => {
                    return Err(format!(
                        "host-side dropins/*.service are no longer supported: {}",
                        path.display()
                    ));
                }
                Some("sh") | Some("py") => {
                    copy_file(&path, &copy_runtime_bin_dir.join(&name), 0o700)?;
                    script_dropins += 1;
                }
                Some("container" | "volume" | "network" | "pod" | "image" | "build") => {
                    return Err(format!(
                        "raw Quadlet drop-ins are no longer supported: {}",
                        path.display()
                    ));
                }
                _ => {
                    hook_log(
                        "proxnix-prestart",
                        "prestart",
                        vmid,
                        &format!("Ignored unknown drop-in type: {name}"),
                    );
                }
            }
        } else if path.is_dir() {
            let dest = managed_dropin_dir.join(&name);
            remove_path_if_exists(&dest)
                .map_err(|err| format!("failed to replace drop-in dir: {err}"))?;
            copy_dir_recursive(&path, &dest)?;
            dir_dropins += 1;
        }
    }
    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!(
            "Processed drop-ins: {nix_dropins} nix file(s), {script_dropins} runtime script(s), {dir_dropins} directory/directories."
        ),
    );
    Ok(())
}

fn copy_dir_recursive(source: &Path, dest: &Path) -> Result<(), String> {
    fs::create_dir_all(dest)
        .map_err(|err| format!("failed to create {}: {err}", dest.display()))?;
    for entry in sorted_dir_entries(source)? {
        let source_path = entry.path();
        let dest_path = dest.join(entry.file_name());
        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|err| format!("failed to stat {}: {err}", source_path.display()))?;
        if metadata.is_dir() {
            copy_dir_recursive(&source_path, &dest_path)?;
        } else if metadata.file_type().is_symlink() {
            let target = fs::read_link(&source_path).map_err(|err| {
                format!("failed to read symlink {}: {err}", source_path.display())
            })?;
            std::os::unix::fs::symlink(target, &dest_path).map_err(|err| {
                format!("failed to create symlink {}: {err}", dest_path.display())
            })?;
        } else if metadata.is_file() {
            fs::copy(&source_path, &dest_path)
                .map_err(|err| format!("failed to copy {}: {err}", source_path.display()))?;
            set_mode(&dest_path, metadata.permissions().mode() & 0o7777)
                .map_err(|err| format!("failed to chmod {}: {err}", dest_path.display()))?;
        }
    }
    Ok(())
}

fn sorted_dir_entries(path: &Path) -> Result<Vec<fs::DirEntry>, String> {
    let mut entries = fs::read_dir(path)
        .map_err(|err| format!("failed to read {}: {err}", path.display()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|err| format!("failed to read {}: {err}", path.display()))?;
    entries.sort_by_key(|entry| entry.file_name());
    Ok(entries)
}

fn hash_tree(dir: &Path) -> io::Result<String> {
    let mut files = Vec::new();
    collect_files(dir, &mut files)?;
    files.sort();
    let mut manifest = Vec::new();
    for path in files {
        let hash = sha256sum_file(&path)?;
        let rel = path.strip_prefix(dir).unwrap_or(&path);
        manifest.extend_from_slice(format!("{hash}  ./{}\n", rel.display()).as_bytes());
    }
    sha256sum_bytes(&manifest)
}

fn collect_files(dir: &Path, files: &mut Vec<PathBuf>) -> io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    let mut entries = fs::read_dir(dir)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let metadata = fs::symlink_metadata(&path)?;
        if metadata.is_dir() {
            collect_files(&path, files)?;
        } else if metadata.is_file() {
            files.push(path);
        }
    }
    Ok(())
}

fn sha256sum_file(path: &Path) -> io::Result<String> {
    let output = Command::new("sha256sum").arg(path).output()?;
    if !output.status.success() {
        return Err(io::Error::other(format!(
            "sha256sum failed for {}",
            path.display()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_owned())
}

fn sha256sum_bytes(bytes: &[u8]) -> io::Result<String> {
    let mut child = Command::new("sha256sum")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()?;
    child.stdin.as_mut().unwrap().write_all(bytes)?;
    let output = child.wait_with_output()?;
    if !output.status.success() {
        return Err(io::Error::other("sha256sum failed for tree manifest"));
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_owned())
}

fn decrypt_host_identity_store_to_file(
    store: &Path,
    out: &Path,
    relay_key: &Path,
) -> io::Result<()> {
    if !relay_key.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("{} missing", relay_key.display()),
        ));
    }
    let output = Command::new("sops")
        .arg("decrypt")
        .arg("--input-type")
        .arg("yaml")
        .arg("--output-type")
        .arg("yaml")
        .arg(store)
        .env_remove("SOPS_AGE_KEY_FILE")
        .env("SOPS_AGE_SSH_PRIVATE_KEY_FILE", relay_key)
        .output()?;
    if !output.status.success() {
        let _ = remove_file_if_exists(out);
        return Err(io::Error::other(
            String::from_utf8_lossy(&output.stderr).into_owned(),
        ));
    }
    let rendered = parse_identity_payload(&String::from_utf8_lossy(&output.stdout))?;
    write_text_file(out, &rendered, 0o600).inspect_err(|_| {
        let _ = remove_file_if_exists(out);
    })
}

fn parse_identity_payload(content: &str) -> io::Result<String> {
    let mut lines = content.lines();
    let Some(first) = lines.next() else {
        return Err(invalid_data("invalid proxnix identity payload"));
    };
    if !first.trim().starts_with("identity: |") {
        return Err(invalid_data("invalid proxnix identity payload"));
    }
    let mut base_indent = None;
    let mut out = String::new();
    for line in lines {
        if line.trim().is_empty() {
            out.push('\n');
            continue;
        }
        let indent = line.len() - line.trim_start_matches(' ').len();
        if base_indent.is_none() {
            if indent == 0 {
                return Err(invalid_data("invalid proxnix identity payload"));
            }
            base_indent = Some(indent);
        }
        if indent < base_indent.unwrap() {
            return Err(invalid_data("invalid proxnix identity payload"));
        }
        out.push_str(&line[base_indent.unwrap()..]);
        out.push('\n');
    }
    Ok(out)
}

fn invalid_data(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn determine_host_root_uid(pve_conf: &Path) -> io::Result<String> {
    let content = fs::read_to_string(pve_conf)?;
    for line in content.lines() {
        let line = line.trim();
        if let Some(value) = line.strip_prefix("lxc.idmap:") {
            let fields = value.split_whitespace().collect::<Vec<_>>();
            if fields.len() >= 3 && fields[0] == "u" && fields[1] == "0" && valid_vmid(fields[2]) {
                return Ok(fields[2].to_owned());
            }
        }
    }
    let raw = parse_pve_conf_raw_content(&content);
    if raw.get("unprivileged").map(String::as_str) == Some("1") {
        Ok("100000".to_owned())
    } else {
        Ok("0".to_owned())
    }
}

fn restrict_stage_tree(
    vmid: &str,
    stage_dir: &Path,
    copy_runtime_bin_dir: &Path,
    host_subuid: &str,
) -> Result<(), String> {
    hook_log(
        "proxnix-prestart",
        "prestart",
        vmid,
        &format!("Restricting staged files for container host root UID {host_subuid}."),
    );
    if env::var("PROXNIX_HOOK_SKIP_STAGE_OWNER_RESTRICT")
        .ok()
        .as_deref()
        == Some("1")
    {
        return Ok(());
    }
    let status = Command::new("chown")
        .arg("-R")
        .arg(host_subuid)
        .arg(stage_dir)
        .status()
        .map_err(|err| format!("failed to run chown: {err}"))?;
    if !status.success() {
        return Err("failed to chown staged files".to_owned());
    }
    chmod_tree(stage_dir, 0o700, 0o400)?;
    chmod_files(copy_runtime_bin_dir, 0o500)?;
    Ok(())
}

fn chmod_tree(root: &Path, dir_mode: u32, file_mode: u32) -> Result<(), String> {
    if !root.exists() {
        return Ok(());
    }
    let metadata = fs::symlink_metadata(root)
        .map_err(|err| format!("failed to stat {}: {err}", root.display()))?;
    if metadata.is_dir() {
        set_mode(root, dir_mode)
            .map_err(|err| format!("failed to chmod {}: {err}", root.display()))?;
        for entry in sorted_dir_entries(root)? {
            chmod_tree(&entry.path(), dir_mode, file_mode)?;
        }
    } else if metadata.is_file() {
        set_mode(root, file_mode)
            .map_err(|err| format!("failed to chmod {}: {err}", root.display()))?;
    }
    Ok(())
}

fn chmod_files(root: &Path, mode: u32) -> Result<(), String> {
    if !root.is_dir() {
        return Ok(());
    }
    for entry in sorted_dir_entries(root)? {
        let path = entry.path();
        if path.is_dir() {
            chmod_files(&path, mode)?;
        } else if path.is_file() {
            set_mode(&path, mode)
                .map_err(|err| format!("failed to chmod {}: {err}", path.display()))?;
        }
    }
    Ok(())
}

fn run_prestart_build(vmid: &str) {
    if env::var("PROXNIX_PRESTART_BUILD").ok().as_deref() == Some("0") {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Skipping pre-start build because PROXNIX_PRESTART_BUILD=0.",
        );
        return;
    }
    if env::var("PROXNIX_PRESTART_GOLDEN_BUILD").ok().as_deref() != Some("0") {
        if command_exists("proxnix-reconcile-build-golden") {
            match Command::new("proxnix-reconcile-build-golden").status() {
                Ok(status) if status.success() => hook_log(
                    "proxnix-prestart",
                    "prestart",
                    vmid,
                    "Golden-template build completed or was already current.",
                ),
                _ => hook_log(
                    "proxnix-prestart",
                    "prestart",
                    vmid,
                    "WARN: golden-template build failed; continuing with the VMID-specific build.",
                ),
            }
        } else {
            hook_log(
                "proxnix-prestart",
                "prestart",
                vmid,
                "WARN: proxnix-reconcile-build-golden is unavailable; continuing with the VMID-specific build.",
            );
        }
    }
    if !command_exists("proxnix-reconcile-build") {
        hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "WARN: proxnix-reconcile-build is unavailable; container will start with the last seeded generation.",
        );
        return;
    }
    match Command::new("proxnix-reconcile-build")
        .arg("--vmid")
        .arg(vmid)
        .status()
    {
        Ok(status) if status.success() => hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "Pre-start build completed or was already current.",
        ),
        _ => hook_log(
            "proxnix-prestart",
            "prestart",
            vmid,
            "WARN: pre-start build failed; container will start with the last seeded generation.",
        ),
    }
}

fn remove_legacy_real_systemd_dir(vmid: &str, rootfs: &Path) -> Result<(), String> {
    let path = rootfs.join("etc/systemd/system");
    if path.is_dir()
        && !fs::symlink_metadata(&path)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
    {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "Removing legacy real /etc/systemd/system directory from guest rootfs.",
        );
        remove_path_if_exists(&path)
            .map_err(|err| format!("failed to remove {}: {err}", path.display()))?;
    }
    Ok(())
}

fn sync_build_input_snapshot(vmid: &str, bind_config_dir: &Path, build_input_dir: &Path) {
    if !command_exists("rsync") {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "WARN: rsync is unavailable; skipping non-authoritative build-input snapshot.",
        );
        return;
    }
    if let Err(err) = fs::create_dir_all(build_input_dir) {
        hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            &format!("WARN: failed to create build-input snapshot dir: {err}"),
        );
        return;
    }
    let source = format!("{}/", bind_config_dir.display());
    let dest = format!("{}/", build_input_dir.display());
    match Command::new("rsync")
        .args([
            "-a",
            "--delete",
            "--chown=0:0",
            "--chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r",
        ])
        .arg(source)
        .arg(dest)
        .status()
    {
        Ok(status) if status.success() => hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "Refreshed non-authoritative build-input snapshot at /var/lib/proxnix/build-input.",
        ),
        _ => hook_log(
            "proxnix-mount",
            "mount",
            vmid,
            "WARN: failed to refresh non-authoritative build-input snapshot.",
        ),
    }
}

fn bind_ro_file(src: &Path, dest: &Path) -> Result<(), String> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .map_err(|err| format!("failed to create {}: {err}", parent.display()))?;
    }
    if is_mountpoint(dest) {
        umount(dest)?;
    }
    remove_file_if_exists(dest)
        .map_err(|err| format!("failed to remove {}: {err}", dest.display()))?;
    fs::File::create(dest).map_err(|err| format!("failed to create {}: {err}", dest.display()))?;
    mount_bind(src, dest)?;
    remount_bind_ro(dest)
}

fn bind_ro_dir(src: &Path, dest: &Path) -> Result<(), String> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .map_err(|err| format!("failed to create {}: {err}", parent.display()))?;
    }
    if is_mountpoint(dest) {
        umount(dest)?;
    }
    remove_path_if_exists(dest)
        .map_err(|err| format!("failed to remove {}: {err}", dest.display()))?;
    fs::create_dir_all(dest)
        .map_err(|err| format!("failed to create {}: {err}", dest.display()))?;
    mount_bind(src, dest)?;
    remount_bind_ro(dest)
}

fn mount_bind(src: &Path, dest: &Path) -> Result<(), String> {
    let status = Command::new("mount")
        .arg("--bind")
        .arg(src)
        .arg(dest)
        .status()
        .map_err(|err| format!("failed to run mount: {err}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "mount --bind failed for {} -> {}",
            src.display(),
            dest.display()
        ))
    }
}

fn remount_bind_ro(dest: &Path) -> Result<(), String> {
    let status = Command::new("mount")
        .args(["-o", "remount,bind,ro"])
        .arg(dest)
        .status()
        .map_err(|err| format!("failed to run mount remount: {err}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("mount remount ro failed for {}", dest.display()))
    }
}

fn is_mountpoint(path: &Path) -> bool {
    Command::new("mountpoint")
        .arg("-q")
        .arg(path)
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn umount(path: &Path) -> Result<(), String> {
    let status = Command::new("umount")
        .arg(path)
        .status()
        .map_err(|err| format!("failed to run umount: {err}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("umount failed for {}", path.display()))
    }
}

fn copy_guest_file(src: &Path, dest: &Path, mode: u32) -> Result<(), String> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)
            .map_err(|err| format!("failed to create {}: {err}", parent.display()))?;
    }
    if is_mountpoint(dest) {
        umount(dest)?;
    }
    remove_path_if_exists(dest)
        .map_err(|err| format!("failed to remove {}: {err}", dest.display()))?;
    let mode_s = format!("{mode:o}");
    let status = Command::new("install")
        .arg("-o")
        .arg("0")
        .arg("-g")
        .arg("0")
        .arg("-m")
        .arg(&mode_s)
        .arg(src)
        .arg(dest)
        .status()
        .map_err(|err| format!("failed to run install: {err}"))?;
    if !status.success() {
        return Err(format!("install failed for {}", dest.display()));
    }
    let metadata = fs::symlink_metadata(dest)
        .map_err(|err| format!("failed to stat {}: {err}", dest.display()))?;
    let actual_mode = metadata.permissions().mode() & 0o777;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.uid() != 0
        || metadata.gid() != 0
        || actual_mode != mode
    {
        return Err(format!(
            "insecure materialized file at {} (uid={} gid={} mode={actual_mode:o}, expected 0:0 {mode:o})",
            dest.display(),
            metadata.uid(),
            metadata.gid()
        ));
    }
    Ok(())
}

fn sync_bound_file_manifest<F>(
    src_dir: &Path,
    dest_dir: &Path,
    manifest: &Path,
    predicate: F,
) -> Result<(), String>
where
    F: Fn(&Path) -> bool,
{
    remove_stale_manifest_entries(src_dir, dest_dir, manifest, false)?;
    let mut current = String::new();
    if src_dir.is_dir() {
        fs::create_dir_all(dest_dir)
            .map_err(|err| format!("failed to create {}: {err}", dest_dir.display()))?;
        for entry in sorted_dir_entries(src_dir)? {
            let src = entry.path();
            if src.is_file() && predicate(&src) {
                let name = entry.file_name().to_string_lossy().into_owned();
                bind_ro_file(&src, &dest_dir.join(&name))?;
                current.push_str(&name);
                current.push('\n');
            }
        }
    }
    write_text_file(manifest, &current, 0o644)
        .map_err(|err| format!("failed to write manifest: {err}"))
}

fn sync_copied_guest_file_manifest<F>(
    src_dir: &Path,
    dest_dir: &Path,
    manifest: &Path,
    mode: u32,
    predicate: F,
) -> Result<(), String>
where
    F: Fn(&Path) -> bool,
{
    fs::create_dir_all(dest_dir)
        .map_err(|err| format!("failed to create {}: {err}", dest_dir.display()))?;
    remove_stale_manifest_entries(src_dir, dest_dir, manifest, false)?;
    let mut current = String::new();
    if src_dir.is_dir() {
        for entry in sorted_dir_entries(src_dir)? {
            let src = entry.path();
            if src.is_file() && predicate(&src) {
                let name = entry.file_name().to_string_lossy().into_owned();
                copy_guest_file(&src, &dest_dir.join(&name), mode)?;
                current.push_str(&name);
                current.push('\n');
            }
        }
    }
    write_text_file(manifest, &current, 0o644)
        .map_err(|err| format!("failed to write manifest: {err}"))
}

fn sync_bound_entry_manifest(
    src_dir: &Path,
    dest_dir: &Path,
    manifest: &Path,
) -> Result<(), String> {
    remove_stale_manifest_entries(src_dir, dest_dir, manifest, true)?;
    let mut current = String::new();
    if src_dir.is_dir() {
        fs::create_dir_all(dest_dir)
            .map_err(|err| format!("failed to create {}: {err}", dest_dir.display()))?;
        for entry in sorted_dir_entries(src_dir)? {
            let src = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if name == ".jj" || name == ".git" {
                continue;
            }
            let dest = dest_dir.join(&name);
            if src.is_dir() {
                bind_ro_dir(&src, &dest)?;
            } else {
                bind_ro_file(&src, &dest)?;
            }
            current.push_str(&name);
            current.push('\n');
        }
    }
    write_text_file(manifest, &current, 0o644)
        .map_err(|err| format!("failed to write manifest: {err}"))
}

fn remove_stale_manifest_entries(
    src_dir: &Path,
    dest_dir: &Path,
    manifest: &Path,
    entries_can_be_dirs: bool,
) -> Result<(), String> {
    let Ok(content) = fs::read_to_string(manifest) else {
        return Ok(());
    };
    for name in content.lines().filter(|line| !line.is_empty()) {
        if !src_dir.join(name).exists() {
            let dest = dest_dir.join(name);
            if is_mountpoint(&dest) {
                umount(&dest)?;
            }
            if entries_can_be_dirs {
                remove_path_if_exists(&dest)
                    .map_err(|err| format!("failed to remove stale {}: {err}", dest.display()))?;
            } else {
                remove_file_if_exists(&dest)
                    .map_err(|err| format!("failed to remove stale {}: {err}", dest.display()))?;
            }
        }
    }
    Ok(())
}

fn is_quadlet_unit_file(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|ext| ext.to_str()),
        Some("container" | "volume" | "network" | "pod" | "image" | "build")
    )
}

fn legacy_apply_config_paths(rootfs: &Path) -> Vec<PathBuf> {
    [
        "etc/systemd/system-generators/proxnix-apply-config-generator",
        "etc/systemd/system/proxnix-apply-config.service",
        "etc/systemd/system/multi-user.target.wants/proxnix-apply-config.service",
        "etc/systemd/system/timers.target.wants/proxnix-apply-config.timer",
        "usr/lib/systemd/system/proxnix-apply-config.service",
        "usr/lib/systemd/system/multi-user.target.wants/proxnix-apply-config.service",
        "usr/lib/systemd/system/proxnix-apply-config.timer",
        "usr/lib/systemd/system/timers.target.wants/proxnix-apply-config.timer",
    ]
    .into_iter()
    .map(|path| rootfs.join(path))
    .collect()
}

fn delete_top_level_matching<F>(dir: &Path, predicate: F) -> Result<(), String>
where
    F: Fn(&Path) -> bool,
{
    if !dir.is_dir() {
        return Ok(());
    }
    for entry in sorted_dir_entries(dir)? {
        let path = entry.path();
        if path.is_file() && predicate(&path) {
            remove_file_if_exists(&path)
                .map_err(|err| format!("failed to remove {}: {err}", path.display()))?;
        }
    }
    Ok(())
}

fn remove_guest_path(path: &Path) -> Result<(), String> {
    if is_mountpoint(path) {
        umount(path)?;
    }
    remove_path_if_exists(path).map_err(|err| format!("failed to remove {}: {err}", path.display()))
}

fn remove_path_if_exists(path: &Path) -> io::Result<()> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(());
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

fn remove_file_if_exists(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err),
    }
}

fn pve_conf_to_nix_main(args: &[String]) -> Result<(), String> {
    let mut pve_conf: Option<PathBuf> = None;
    let mut out_dir: Option<PathBuf> = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--pve-conf" => {
                index += 1;
                pve_conf = args.get(index).map(PathBuf::from);
            }
            "--out-dir" => {
                index += 1;
                out_dir = args.get(index).map(PathBuf::from);
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown pve-conf-to-nix argument: {other}")),
        }
        index += 1;
    }

    let pve_conf = pve_conf.ok_or("--pve-conf is required")?;
    let out_dir = out_dir.ok_or("--out-dir is required")?;
    let rendered = generate_proxmox_nix(
        &parse_pve_conf(&pve_conf)
            .map_err(|err| format!("failed to read {}: {err}", pve_conf.display()))?,
    );

    fs::create_dir_all(&out_dir)
        .map_err(|err| format!("failed to create {}: {err}", out_dir.display()))?;
    write_if_changed(&out_dir.join("proxmox.nix"), &rendered).map_err(|err| {
        format!(
            "failed to write {}: {err}",
            out_dir.join("proxmox.nix").display()
        )
    })?;
    Ok(())
}

#[derive(Debug, Default, PartialEq, Eq)]
struct ProxmoxNixData {
    hostname: Option<String>,
    nameservers: Vec<String>,
    search_domain: Option<String>,
    ssh_keys: Vec<String>,
}

fn parse_pve_conf(path: &Path) -> io::Result<ProxmoxNixData> {
    let content = fs::read_to_string(path)?;
    Ok(parse_pve_conf_content(&content))
}

fn parse_pve_conf_content(content: &str) -> ProxmoxNixData {
    let mut raw = HashMap::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
            continue;
        }
        let Some((key, value)) = line.split_once(": ") else {
            continue;
        };
        raw.insert(key.trim().to_owned(), value.trim().to_owned());
    }

    ProxmoxNixData {
        hostname: raw
            .get("hostname")
            .filter(|value| !value.is_empty())
            .cloned(),
        nameservers: raw
            .get("nameserver")
            .map(|value| value.split_whitespace().map(str::to_owned).collect())
            .unwrap_or_default(),
        search_domain: raw
            .get("searchdomain")
            .filter(|value| !value.is_empty())
            .cloned(),
        ssh_keys: decode_ssh_public_keys(raw.get("ssh-public-keys").map(String::as_str)),
    }
}

fn generate_proxmox_nix(data: &ProxmoxNixData) -> String {
    generate_proxmox_nix_with_header(
        data,
        &format!(
            "# Generated by pve-conf-to-nix.py {} do not edit by hand.",
            '\u{2014}'
        ),
    )
}

fn generate_proxmox_nix_with_header(data: &ProxmoxNixData, header: &str) -> String {
    let mut lines = vec![header.to_owned(), "{ lib, ... }: {".to_owned()];

    if let Some(hostname) = &data.hostname {
        lines.push(format!(
            "  networking.hostName = lib.mkForce {};",
            nix_str(hostname)
        ));
    }
    if !data.nameservers.is_empty() {
        lines.push(format!(
            "  networking.nameservers = lib.mkForce {};",
            nix_str_list(&data.nameservers)
        ));
    }
    if let Some(search_domain) = &data.search_domain {
        lines.push(format!(
            "  networking.search = lib.mkForce [ {} ];",
            nix_str(search_domain)
        ));
    }
    if !data.ssh_keys.is_empty() {
        lines.push(format!(
            "  users.users.root.openssh.authorizedKeys.keys = lib.mkForce {};",
            nix_str_list(&data.ssh_keys)
        ));
    }

    lines.push("}".to_owned());
    lines.push(String::new());
    lines.join("\n")
}

fn write_if_changed(path: &Path, content: &str) -> io::Result<()> {
    if path.exists() && fs::read_to_string(path).is_ok_and(|existing| existing == content) {
        return Ok(());
    }
    fs::write(path, content)
}

fn decode_ssh_public_keys(raw_value: Option<&str>) -> Vec<String> {
    let Some(raw_value) = raw_value else {
        return Vec::new();
    };
    let decoded = percent_decode_lossy(raw_value).replace("\\n", "\n");
    decoded
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.trim_start().starts_with('#'))
        .map(str::to_owned)
        .collect()
}

fn percent_decode_lossy(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;

    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            if let (Some(high), Some(low)) =
                (hex_value(bytes[index + 1]), hex_value(bytes[index + 2]))
            {
                decoded.push((high << 4) | low);
                index += 3;
                continue;
            }
        }
        decoded.push(bytes[index]);
        index += 1;
    }

    String::from_utf8_lossy(&decoded).into_owned()
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn nix_str(value: &str) -> String {
    let escaped = value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace("${", "\\${");
    format!("\"{escaped}\"")
}

fn nix_str_list(items: &[String]) -> String {
    let rendered = items
        .iter()
        .map(|item| nix_str(item))
        .collect::<Vec<_>>()
        .join(" ");
    format!("[ {rendered} ]")
}

#[derive(Debug, PartialEq)]
struct AuthorityContainerManifest {
    vmid: String,
    hostname: Option<String>,
    pve: Map<String, Value>,
    placement: Map<String, Value>,
}

fn authority_render_main(args: &[String]) -> Result<(), String> {
    let mut root = PathBuf::from("/var/lib/proxnix");
    let mut authority: Option<PathBuf> = None;
    let mut pve_lxc_dir = PathBuf::from("/etc/pve/lxc");
    let mut node_name = default_node_name();
    let mut print_manifest = false;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--root" => root = PathBuf::from(take_arg(args, &mut index, "--root")?),
            "--authority" => {
                authority = Some(PathBuf::from(take_arg(args, &mut index, "--authority")?))
            }
            "--pve-lxc-dir" => {
                pve_lxc_dir = PathBuf::from(take_arg(args, &mut index, "--pve-lxc-dir")?)
            }
            "--node-name" => node_name = take_arg(args, &mut index, "--node-name")?,
            "--print-manifest" => print_manifest = true,
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown authority render argument: {other}")),
        }
        index += 1;
    }

    let authority = authority.unwrap_or_else(|| root.join("authority"));
    let manifests = render_authority(&root, &authority, &pve_lxc_dir, &node_name)
        .map_err(|err| format!("failed to render authority: {err}"))?;
    if print_manifest {
        print!(
            "{}",
            fs::read_to_string(authority.join("generated/node-manifest.nix"))
                .map_err(|err| format!("failed to read node manifest: {err}"))?
        );
    } else {
        println!(
            "rendered {} container(s) for node {} into {}",
            manifests.len(),
            node_name,
            authority.display()
        );
    }
    Ok(())
}

fn render_authority(
    root: &Path,
    authority: &Path,
    pve_lxc_dir: &Path,
    node_name: &str,
) -> io::Result<Vec<AuthorityContainerManifest>> {
    let generated = authority.join("generated");
    let legacy = generated.join("legacy");
    let modules = authority.join("modules");
    let containers_dir = root.join("containers");

    fs::create_dir_all(authority)?;
    fs::create_dir_all(&generated)?;
    fs::create_dir_all(&modules)?;
    fs::create_dir_all(&legacy)?;

    for filename in ["base.nix", "common.nix", "security-policy.nix"] {
        let source = root.join(filename);
        if !source.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("required proxnix module missing: {}", source.display()),
            ));
        }
        copy_if_present(&source, &modules.join(filename))?;
    }

    let has_site = copy_if_present(&root.join("site.nix"), &legacy.join("site.nix"))?;
    copy_if_present(&root.join("flake.lock"), &authority.join("flake.lock"))?;
    atomic_write(
        &modules.join("proxnix-guest-base.nix"),
        &[
            "# Generated by proxnix-authority-render; do not edit by hand.",
            "{ ... }: {",
            "  imports = [",
            "    ./base.nix",
            "    ./common.nix",
            "    ./security-policy.nix",
            "  ];",
            "  system.stateVersion = \"25.11\";",
            "}",
            "",
        ]
        .join("\n"),
        0o644,
    )?;

    let mut vmids = Vec::new();
    if containers_dir.exists() {
        for entry in fs::read_dir(&containers_dir)? {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if entry.file_type()?.is_dir() && name.chars().all(|ch| ch.is_ascii_digit()) {
                vmids.push(name);
            }
        }
        vmids.sort();
    }

    let mut manifests = Vec::new();
    for vmid in vmids {
        let pve_conf = pve_lxc_dir.join(format!("{vmid}.conf"));
        let raw = if pve_conf.exists() {
            parse_pve_conf_raw(&pve_conf)?
        } else {
            HashMap::new()
        };
        let container_out = generated.join("containers").join(&vmid);
        let dropins_out = container_out.join("dropins");
        fs::create_dir_all(&dropins_out)?;

        let mut dropin_names = Vec::new();
        let dropin_dir = containers_dir.join(&vmid).join("dropins");
        if dropin_dir.exists() {
            let mut dropins = fs::read_dir(&dropin_dir)?.collect::<io::Result<Vec<_>>>()?;
            dropins.sort_by_key(|entry| entry.file_name());
            for source in dropins {
                let source_path = source.path();
                if source.file_type()?.is_file()
                    && source_path.extension().and_then(|ext| ext.to_str()) == Some("nix")
                {
                    let name = source.file_name().to_string_lossy().into_owned();
                    copy_if_present(&source_path, &dropins_out.join(&name))?;
                    dropin_names.push(name);
                }
            }
        }

        atomic_write(
            &container_out.join("proxmox.nix"),
            &authority_proxmox_module(&raw),
            0o644,
        )?;
        atomic_write(
            &container_out.join("modules.nix"),
            &render_modules_nix(has_site, &dropin_names),
            0o644,
        )?;

        let mut placement = Map::new();
        placement.insert("local".to_owned(), Value::Bool(false));
        placement.insert("node".to_owned(), Value::Null);
        placement.insert(
            "observedPveConfig".to_owned(),
            Value::Bool(pve_conf.exists()),
        );
        manifests.push(AuthorityContainerManifest {
            vmid,
            hostname: raw.get("hostname").cloned(),
            pve: normalize_pve(&raw),
            placement,
        });
    }

    atomic_write(
        &authority.join("flake.nix"),
        &render_authority_flake(node_name),
        0o644,
    )?;
    atomic_write(
        &generated.join("node-manifest.nix"),
        &render_node_manifest(node_name, &manifests, load_source_revision(root)),
        0o644,
    )?;
    Ok(manifests)
}

fn parse_pve_conf_raw(path: &Path) -> io::Result<HashMap<String, String>> {
    Ok(parse_pve_conf_raw_content(&fs::read_to_string(path)?))
}

fn parse_pve_conf_raw_content(content: &str) -> HashMap<String, String> {
    let mut raw = HashMap::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
            continue;
        }
        let Some((key, value)) = line.split_once(": ") else {
            continue;
        };
        raw.insert(key.trim().to_owned(), value.trim().to_owned());
    }
    raw
}

fn normalize_pve(raw: &HashMap<String, String>) -> Map<String, Value> {
    let mut pve = Map::new();
    for key in ["ostype", "hostname", "rootfs", "net0", "features"] {
        if let Some(value) = raw.get(key).filter(|value| !value.is_empty()) {
            pve.insert(key.to_owned(), Value::String(value.clone()));
        }
    }
    for key in ["memory", "swap", "cores"] {
        if let Some(value) = raw.get(key).filter(|value| !value.is_empty()) {
            let rendered = value
                .parse::<i64>()
                .map(|number| Value::Number(number.into()))
                .unwrap_or_else(|_| Value::String(value.clone()));
            pve.insert(key.to_owned(), rendered);
        }
    }
    if let Some(unprivileged) = raw
        .get("unprivileged")
        .and_then(|value| parse_pve_bool(Some(value)))
    {
        pve.insert("unprivileged".to_owned(), Value::Bool(unprivileged));
    }
    pve
}

fn parse_pve_bool(raw: Option<&String>) -> Option<bool> {
    raw.map(|value| matches!(value.as_str(), "1" | "true" | "yes" | "on"))
}

fn authority_proxmox_module(raw: &HashMap<String, String>) -> String {
    let data = ProxmoxNixData {
        hostname: raw
            .get("hostname")
            .filter(|value| !value.is_empty())
            .cloned(),
        nameservers: raw
            .get("nameserver")
            .map(|value| value.split_whitespace().map(str::to_owned).collect())
            .unwrap_or_default(),
        search_domain: raw
            .get("searchdomain")
            .filter(|value| !value.is_empty())
            .cloned(),
        ssh_keys: decode_ssh_public_keys(raw.get("ssh-public-keys").map(String::as_str)),
    };
    generate_proxmox_nix_with_header(
        &data,
        "# Generated by proxnix-authority-render; do not edit by hand.",
    )
}

fn render_modules_nix(has_site: bool, dropins: &[String]) -> String {
    let mut lines = vec![
        "# Generated by proxnix-authority-render; do not edit by hand.".to_owned(),
        "[".to_owned(),
        "  ../../../modules/proxnix-guest-base.nix".to_owned(),
    ];
    if has_site {
        lines.push("  ../../legacy/site.nix".to_owned());
    }
    lines.push("  ./proxmox.nix".to_owned());
    for dropin in dropins {
        lines.push(format!("  ./dropins/{dropin}"));
    }
    lines.push("]".to_owned());
    lines.push(String::new());
    lines.join("\n")
}

fn render_pve_attrs(pve: &Map<String, Value>) -> String {
    if pve.is_empty() {
        return "{}".to_owned();
    }
    let mut lines = vec!["{".to_owned()];
    let mut keys = pve.keys().collect::<Vec<_>>();
    keys.sort();
    for key in keys {
        lines.push(format!(
            "      {} = {};",
            nix_attr_name(key),
            json_to_nix(&pve[key], 6)
        ));
    }
    lines.push("    }".to_owned());
    lines.join("\n")
}

fn render_node_manifest(
    node_name: &str,
    containers: &[AuthorityContainerManifest],
    source_revision: Option<Value>,
) -> String {
    let local_vmids = containers
        .iter()
        .filter(|container| container.placement.get("local").and_then(Value::as_bool) == Some(true))
        .map(|container| container.vmid.as_str())
        .collect::<Vec<_>>();
    let mut lines = vec![
        "# Generated by proxnix-authority-render; do not edit by hand.".to_owned(),
        "{ self, lib }:".to_owned(),
        "let".to_owned(),
        "  systemPath = name: builtins.unsafeDiscardStringContext \"${self.nixosConfigurations.${name}.config.system.build.toplevel}\";".to_owned(),
        "in {".to_owned(),
        format!("  nodeName = {};", nix_str(node_name)),
        "  vmids = [".to_owned(),
    ];
    for container in containers {
        lines.push(format!("    {}", nix_str(&container.vmid)));
    }
    lines.push("  ];".to_owned());
    lines.push("  localVmids = [".to_owned());
    for vmid in local_vmids {
        lines.push(format!("    {}", nix_str(vmid)));
    }
    lines.push("  ];".to_owned());
    lines.push("  containers = {".to_owned());

    let source = source_revision
        .as_ref()
        .map(|value| json_to_nix(value, 4))
        .unwrap_or_else(|| "null".to_owned());
    for container in containers {
        let hostname = container
            .hostname
            .clone()
            .unwrap_or_else(|| format!("ct{}", container.vmid));
        let system_attr = format!(
            "nixosConfigurations.ct{}.config.system.build.toplevel",
            container.vmid
        );
        lines.push(format!("    {} = {{", nix_attr_name(&container.vmid)));
        lines.push(format!("      vmid = {};", container.vmid));
        lines.push(format!("      hostname = {};", nix_str(&hostname)));
        lines.push(format!("      sourceRevision = {source};"));
        lines.push(format!("      systemAttr = {};", nix_str(&system_attr)));
        lines.push(format!(
            "      system = systemPath {};",
            nix_str(&format!("ct{}", container.vmid))
        ));
        lines.push(format!("      pve = {};", render_pve_attrs(&container.pve)));
        lines.push(format!(
            "      placement = {};",
            json_to_nix(&Value::Object(container.placement.clone()), 6)
        ));
        lines.push("    };".to_owned());
    }
    lines.push("  };".to_owned());
    lines.push("}".to_owned());
    lines.push(String::new());
    lines.join("\n")
}

fn json_to_nix(value: &Value, indent: usize) -> String {
    let pad = " ".repeat(indent);
    let inner = " ".repeat(indent + 2);
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(value) => {
            if *value {
                "true".to_owned()
            } else {
                "false".to_owned()
            }
        }
        Value::Number(value) => value.to_string(),
        Value::String(value) => nix_str(value),
        Value::Array(items) => {
            if items.is_empty() {
                "[]".to_owned()
            } else {
                format!(
                    "[ {} ]",
                    items
                        .iter()
                        .map(|item| json_to_nix(item, indent))
                        .collect::<Vec<_>>()
                        .join(" ")
                )
            }
        }
        Value::Object(map) => {
            if map.is_empty() {
                "{}".to_owned()
            } else {
                let mut lines = vec!["{".to_owned()];
                let mut keys = map.keys().collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    lines.push(format!(
                        "{inner}{} = {};",
                        nix_attr_name(key),
                        json_to_nix(&map[key], indent + 2)
                    ));
                }
                lines.push(format!("{pad}}}"));
                lines.join("\n")
            }
        }
    }
}

fn render_authority_flake(node_name: &str) -> String {
    let lines = vec![
        "# Generated by proxnix-authority-render; do not edit by hand.".to_owned(),
        "{".to_owned(),
        "  description = \"proxnix generated compatibility authority\";".to_owned(),
        String::new(),
        "  inputs.nixpkgs.url = \"github:NixOS/nixpkgs/nixos-25.11\";".to_owned(),
        String::new(),
        "  outputs = { self, nixpkgs }:".to_owned(),
        "    let".to_owned(),
        "      system = \"x86_64-linux\";".to_owned(),
        "      lib = nixpkgs.lib;".to_owned(),
        "      manifest = import ./generated/node-manifest.nix { inherit self lib; };".to_owned(),
        "      goldenTemplateModules =".to_owned(),
        "        [ ./modules/proxnix-guest-base.nix ]".to_owned(),
        "        ++ lib.optional (builtins.pathExists ./generated/legacy/site.nix) ./generated/legacy/site.nix;".to_owned(),
        "      mkCt = vmid: lib.nixosSystem {".to_owned(),
        "        inherit system;".to_owned(),
        "        modules = import ./generated/containers/${vmid}/modules.nix;".to_owned(),
        "      };".to_owned(),
        "    in {".to_owned(),
        "      nixosConfigurations = {".to_owned(),
        "        proxnix-golden-template = lib.nixosSystem {".to_owned(),
        "          inherit system;".to_owned(),
        "          modules = goldenTemplateModules;".to_owned(),
        "        };".to_owned(),
        "      } // builtins.listToAttrs (map (vmid: {".to_owned(),
        "        name = \"ct${vmid}\";".to_owned(),
        "        value = mkCt vmid;".to_owned(),
        "      }) manifest.vmids);".to_owned(),
        String::new(),
        "      proxnix.containers = manifest.containers;".to_owned(),
        format!("      proxnix.nodes.{} = manifest;", nix_attr_name(node_name)),
        "    };".to_owned(),
        "}".to_owned(),
        String::new(),
    ];
    lines.join("\n")
}

fn load_source_revision(root: &Path) -> Option<Value> {
    let path = root.join("publish-revision.json");
    let value = serde_json::from_str::<Value>(&fs::read_to_string(path).ok()?).ok()?;
    value.is_object().then_some(value)
}

fn atomic_write(path: &Path, content: &str, mode: u32) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_file_name(format!(
        "{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("tmp")
    ));
    fs::write(&tmp, content)?;
    set_mode(&tmp, mode)?;
    fs::rename(tmp, path)
}

fn copy_if_present(source: &Path, dest: &Path) -> io::Result<bool> {
    if !source.exists() {
        return Ok(false);
    }
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::copy(source, dest)?;
    Ok(true)
}

fn nix_attr_name(value: &str) -> String {
    let mut chars = value.chars();
    let Some(first) = chars.next() else {
        return nix_str(value);
    };
    let valid_first = first.is_ascii_alphabetic() || first == '_';
    let valid_rest = chars.all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '\'' | '-'));
    if valid_first && valid_rest {
        value.to_owned()
    } else {
        nix_str(value)
    }
}

fn default_node_name() -> String {
    Command::new("hostname")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .and_then(|name| name.trim().split('.').next().map(str::to_owned))
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "localhost".to_owned())
}

const DEFAULT_STATE_DB: &str = "/var/lib/proxnix/state/proxnix-reconciler.sqlite";

const STATE_SCHEMA: &str = r#"
create table if not exists container_observations (
  vmid integer primary key,
  node text not null,
  desired_system text,
  current_system text,
  container_is_local integer not null,
  last_phase text,
  last_status text,
  last_error text,
  updated_at text not null
);

create table if not exists closure_observations (
  store_path text primary key,
  host_has_closure integer,
  container_has_closure integer,
  protected_by_host_gc_root integer not null default 0,
  gc_root_path text,
  updated_at text not null
);

create table if not exists deployment_attempts (
  id integer primary key autoincrement,
  vmid integer not null,
  store_path text,
  phase text not null,
  status text not null,
  error text,
  started_at text not null,
  finished_at text
);

create index if not exists deployment_attempts_vmid_idx
  on deployment_attempts(vmid);
"#;

fn state_main(args: &[String]) -> Result<(), String> {
    state_main_inner(args).map_err(|err| format!("state command failed: {err}"))
}

fn state_main_inner(args: &[String]) -> HostResult<()> {
    let mut db_path = PathBuf::from(DEFAULT_STATE_DB);
    let mut index = 0;

    while index < args.len() {
        match args[index].as_str() {
            "--db" => {
                index += 1;
                db_path = PathBuf::from(
                    args.get(index)
                        .ok_or_else(|| invalid_input("--db requires a value"))?,
                );
                index += 1;
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            _ => break,
        }
    }

    let command = args
        .get(index)
        .ok_or_else(|| invalid_input("state subcommand is required"))?;
    let command_args = &args[index + 1..];
    let conn = connect_state_db(&db_path)?;

    match command.as_str() {
        "init" => init_state_db(&conn)?,
        "observe-container" => {
            let observation = parse_container_observation(command_args).map_err(invalid_input)?;
            observe_container(&conn, &observation)?;
        }
        "observe-closure" => {
            let observation = parse_closure_observation(command_args).map_err(invalid_input)?;
            observe_closure(&conn, &observation)?;
        }
        "record-attempt" => {
            let attempt = parse_deployment_attempt(command_args).map_err(invalid_input)?;
            let attempt_id = record_deployment_attempt(&conn, &attempt)?;
            println!("{attempt_id}");
        }
        other => return Err(invalid_input(format!("unknown state subcommand: {other}")).into()),
    }

    Ok(())
}

fn invalid_input(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message.into())
}

#[derive(Debug, PartialEq, Eq)]
struct ContainerObservation {
    vmid: i64,
    node: String,
    desired_system: Option<String>,
    current_system: Option<String>,
    container_is_local: bool,
    last_phase: Option<String>,
    last_status: Option<String>,
    last_error: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
struct ClosureObservation {
    store_path: String,
    host_has_closure: Option<bool>,
    container_has_closure: Option<bool>,
    protected_by_host_gc_root: Option<bool>,
    gc_root_path: Option<String>,
}

#[derive(Debug, PartialEq, Eq)]
struct DeploymentAttempt {
    vmid: i64,
    store_path: Option<String>,
    phase: String,
    status: String,
    error: Option<String>,
    finished_at: Option<String>,
}

fn parse_container_observation(args: &[String]) -> Result<ContainerObservation, String> {
    let mut vmid = None;
    let mut node = None;
    let mut desired_system = None;
    let mut current_system = None;
    let mut container_is_local = None;
    let mut last_phase = None;
    let mut last_status = None;
    let mut last_error = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => vmid = Some(parse_i64_arg(args, &mut index, "--vmid")?),
            "--node" => node = Some(take_arg(args, &mut index, "--node")?),
            "--desired-system" => {
                desired_system = Some(take_arg(args, &mut index, "--desired-system")?)
            }
            "--current-system" => {
                current_system = Some(take_arg(args, &mut index, "--current-system")?)
            }
            "--container-is-local" => {
                container_is_local = Some(parse_bool_arg(args, &mut index, "--container-is-local")?)
            }
            "--last-phase" => last_phase = Some(take_arg(args, &mut index, "--last-phase")?),
            "--last-status" => last_status = Some(take_arg(args, &mut index, "--last-status")?),
            "--last-error" => last_error = Some(take_arg(args, &mut index, "--last-error")?),
            other => return Err(format!("unknown observe-container argument: {other}")),
        }
        index += 1;
    }

    Ok(ContainerObservation {
        vmid: vmid.ok_or("--vmid is required")?,
        node: node.ok_or("--node is required")?,
        desired_system,
        current_system,
        container_is_local: container_is_local.ok_or("--container-is-local is required")?,
        last_phase,
        last_status,
        last_error,
    })
}

fn parse_closure_observation(args: &[String]) -> Result<ClosureObservation, String> {
    let mut store_path = None;
    let mut host_has_closure = None;
    let mut container_has_closure = None;
    let mut protected_by_host_gc_root = None;
    let mut gc_root_path = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--store-path" => store_path = Some(take_arg(args, &mut index, "--store-path")?),
            "--host-has-closure" => {
                host_has_closure = Some(parse_bool_arg(args, &mut index, "--host-has-closure")?)
            }
            "--container-has-closure" => {
                container_has_closure =
                    Some(parse_bool_arg(args, &mut index, "--container-has-closure")?)
            }
            "--protected-by-host-gc-root" => {
                protected_by_host_gc_root = Some(parse_bool_arg(
                    args,
                    &mut index,
                    "--protected-by-host-gc-root",
                )?)
            }
            "--gc-root-path" => gc_root_path = Some(take_arg(args, &mut index, "--gc-root-path")?),
            other => return Err(format!("unknown observe-closure argument: {other}")),
        }
        index += 1;
    }

    Ok(ClosureObservation {
        store_path: store_path.ok_or("--store-path is required")?,
        host_has_closure,
        container_has_closure,
        protected_by_host_gc_root,
        gc_root_path,
    })
}

fn parse_deployment_attempt(args: &[String]) -> Result<DeploymentAttempt, String> {
    let mut vmid = None;
    let mut store_path = None;
    let mut phase = None;
    let mut status = None;
    let mut error = None;
    let mut finished_at = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => vmid = Some(parse_i64_arg(args, &mut index, "--vmid")?),
            "--store-path" => store_path = Some(take_arg(args, &mut index, "--store-path")?),
            "--phase" => phase = Some(take_arg(args, &mut index, "--phase")?),
            "--status" => status = Some(take_arg(args, &mut index, "--status")?),
            "--error" => error = Some(take_arg(args, &mut index, "--error")?),
            "--finished-at" => finished_at = Some(take_arg(args, &mut index, "--finished-at")?),
            other => return Err(format!("unknown record-attempt argument: {other}")),
        }
        index += 1;
    }

    Ok(DeploymentAttempt {
        vmid: vmid.ok_or("--vmid is required")?,
        store_path,
        phase: phase.ok_or("--phase is required")?,
        status: status.ok_or("--status is required")?,
        error,
        finished_at,
    })
}

fn take_arg(args: &[String], index: &mut usize, flag: &str) -> Result<String, String> {
    *index += 1;
    args.get(*index)
        .cloned()
        .ok_or_else(|| format!("{flag} requires a value"))
}

fn parse_i64_arg(args: &[String], index: &mut usize, flag: &str) -> Result<i64, String> {
    take_arg(args, index, flag)?
        .parse::<i64>()
        .map_err(|err| format!("{flag} must be an integer: {err}"))
}

fn parse_bool_arg(args: &[String], index: &mut usize, flag: &str) -> Result<bool, String> {
    parse_state_bool(&take_arg(args, index, flag)?)
}

fn parse_state_bool(value: &str) -> Result<bool, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "y" => Ok(true),
        "0" | "false" | "no" | "n" => Ok(false),
        _ => Err(format!("invalid boolean: {value}")),
    }
}

fn connect_state_db(db_path: &Path) -> HostResult<Connection> {
    if let Some(parent) = db_path.parent() {
        fs::create_dir_all(parent)?;
    }
    Ok(Connection::open(db_path)?)
}

fn init_state_db(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(STATE_SCHEMA)
}

fn observe_container(
    conn: &Connection,
    observation: &ContainerObservation,
) -> rusqlite::Result<()> {
    init_state_db(conn)?;
    conn.execute(
        r#"
        insert into container_observations (
          vmid, node, desired_system, current_system, container_is_local,
          last_phase, last_status, last_error, updated_at
        )
        values (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
        on conflict(vmid) do update set
          node = excluded.node,
          desired_system = excluded.desired_system,
          current_system = excluded.current_system,
          container_is_local = excluded.container_is_local,
          last_phase = excluded.last_phase,
          last_status = excluded.last_status,
          last_error = excluded.last_error,
          updated_at = excluded.updated_at
        "#,
        params![
            observation.vmid,
            observation.node.as_str(),
            observation.desired_system.as_deref(),
            observation.current_system.as_deref(),
            bool_to_int(Some(observation.container_is_local)),
            observation.last_phase.as_deref(),
            observation.last_status.as_deref(),
            observation.last_error.as_deref(),
            utc_now_seconds_z(),
        ],
    )?;
    Ok(())
}

fn observe_closure(conn: &Connection, observation: &ClosureObservation) -> rusqlite::Result<()> {
    init_state_db(conn)?;
    let protected_by_host_gc_root = observation.protected_by_host_gc_root.unwrap_or_else(|| {
        gcroot_protects_store_path(&observation.store_path, observation.gc_root_path.as_deref())
    });
    conn.execute(
        r#"
        insert into closure_observations (
          store_path, host_has_closure, container_has_closure,
          protected_by_host_gc_root, gc_root_path, updated_at
        )
        values (?1, ?2, ?3, ?4, ?5, ?6)
        on conflict(store_path) do update set
          host_has_closure = excluded.host_has_closure,
          container_has_closure = excluded.container_has_closure,
          protected_by_host_gc_root = excluded.protected_by_host_gc_root,
          gc_root_path = excluded.gc_root_path,
          updated_at = excluded.updated_at
        "#,
        params![
            observation.store_path.as_str(),
            bool_to_int(observation.host_has_closure),
            bool_to_int(observation.container_has_closure),
            bool_to_int(Some(protected_by_host_gc_root)),
            observation.gc_root_path.as_deref(),
            utc_now_seconds_z(),
        ],
    )?;
    Ok(())
}

fn record_deployment_attempt(
    conn: &Connection,
    attempt: &DeploymentAttempt,
) -> rusqlite::Result<i64> {
    init_state_db(conn)?;
    conn.execute(
        r#"
        insert into deployment_attempts (
          vmid, store_path, phase, status, error, started_at, finished_at
        )
        values (?1, ?2, ?3, ?4, ?5, ?6, ?7)
        "#,
        params![
            attempt.vmid,
            attempt.store_path.as_deref(),
            attempt.phase.as_str(),
            attempt.status.as_str(),
            attempt.error.as_deref(),
            utc_now_seconds_z(),
            attempt.finished_at.as_deref(),
        ],
    )?;
    Ok(conn.last_insert_rowid())
}

fn bool_to_int(value: Option<bool>) -> Option<i64> {
    value.map(|value| if value { 1 } else { 0 })
}

fn gcroot_protects_store_path(store_path: &str, gc_root_path: Option<&str>) -> bool {
    let Some(gc_root_path) = gc_root_path else {
        return false;
    };
    let root = Path::new(gc_root_path);
    if !fs::symlink_metadata(root).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return false;
    }
    if fs::read_link(root).ok().as_deref() != Some(Path::new(store_path)) {
        return false;
    }
    let Some(nix_store) = find_in_path("nix-store") else {
        return false;
    };
    let Ok(output) = Command::new(nix_store)
        .args(["--query", "--roots", store_path])
        .stderr(Stdio::null())
        .output()
    else {
        return false;
    };
    if !output.status.success() {
        return false;
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .any(|line| line == gc_root_path)
}

fn find_in_path(command: &str) -> Option<PathBuf> {
    if command.contains('/') {
        let path = PathBuf::from(command);
        return path.exists().then_some(path);
    }
    env::var_os("PATH").and_then(|path| {
        env::split_paths(&path)
            .map(|dir| dir.join(command))
            .find(|path| path.is_file())
    })
}

const PODMAN_LABEL_KEY: &str = "proxnix.managed";

fn reconcile_podman_secrets_main(args: &[String]) -> Result<(), String> {
    let mut rootfs: Option<PathBuf> = None;
    let mut vmid: Option<String> = None;
    let mut secrets_dir: Option<PathBuf> = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--rootfs" => {
                index += 1;
                rootfs = args.get(index).map(PathBuf::from);
            }
            "--vmid" => {
                index += 1;
                vmid = args.get(index).cloned();
            }
            "--secrets-dir" => {
                index += 1;
                secrets_dir = args.get(index).map(PathBuf::from);
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown podman-secrets argument: {other}")),
        }
        index += 1;
    }

    reconcile_podman_secrets(
        &rootfs.ok_or("--rootfs is required")?,
        &vmid.ok_or("--vmid is required")?,
        &secrets_dir.ok_or("--secrets-dir is required")?,
    )
    .map_err(|err| format!("failed to reconcile Podman secrets: {err}"))
}

fn reconcile_podman_secrets(rootfs: &Path, vmid: &str, secrets_dir: &Path) -> io::Result<()> {
    let live_names = if secrets_dir.is_dir() {
        top_level_yaml_keys(&secrets_dir.join("effective.sops.yaml"))?
    } else {
        BTreeSet::new()
    };

    let secrets_json_path = rootfs.join("var/lib/containers/storage/secrets/secrets.json");
    let ids_dir = rootfs.join("etc/secrets/.ids");

    fs::create_dir_all(&ids_dir)?;
    let secrets_json_dir = secrets_json_path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "secrets.json has no parent"))?;
    fs::create_dir_all(secrets_json_dir)?;
    set_mode(secrets_json_dir, 0o700)?;

    let mut data = load_json_object(&secrets_json_path);
    ensure_object_field(&mut data, "secrets");
    ensure_object_field(&mut data, "nameToID");
    ensure_object_field(&mut data, "idToName");

    let now = utc_now_isoformat();
    let mut changed = false;

    let stale_ids = data
        .get("secrets")
        .and_then(Value::as_object)
        .map(|secrets| {
            secrets
                .iter()
                .filter_map(|(sid, entry)| {
                    let entry = entry.as_object()?;
                    let labels = entry.get("labels")?.as_object()?;
                    let managed =
                        labels.get(PODMAN_LABEL_KEY).and_then(Value::as_str) == Some("true");
                    let name = entry.get("name").and_then(Value::as_str).unwrap_or("");
                    if managed && !live_names.contains(name) {
                        Some((sid.clone(), name.to_owned()))
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    for (sid, name) in stale_ids {
        object_field_mut(&mut data, "secrets").remove(&sid);
        object_field_mut(&mut data, "nameToID").remove(&name);
        object_field_mut(&mut data, "idToName").remove(&sid);
        match fs::remove_file(ids_dir.join(&sid)) {
            Ok(()) => {}
            Err(err) if err.kind() == io::ErrorKind::NotFound => {}
            Err(err) => return Err(err),
        }
        eprintln!("[proxnix-mount][{vmid}] Unregistered removed secret: {name}");
        changed = true;
    }

    for name in &live_names {
        let sid = podman_secret_id(vmid, name);
        let created_at = data
            .get("secrets")
            .and_then(Value::as_object)
            .and_then(|secrets| secrets.get(&sid))
            .and_then(Value::as_object)
            .and_then(|entry| entry.get("createdAt"))
            .and_then(Value::as_str)
            .unwrap_or(&now)
            .to_owned();

        let entry = json!({
            "name": name,
            "id": sid,
            "labels": { PODMAN_LABEL_KEY: "true" },
            "metadata": {},
            "createdAt": created_at,
            "updatedAt": now,
            "driver": "shell",
            "driverOptions": podman_driver_options(),
        });

        if data
            .get("secrets")
            .and_then(Value::as_object)
            .and_then(|secrets| secrets.get(&sid))
            != Some(&entry)
        {
            object_field_mut(&mut data, "secrets").insert(sid.clone(), entry);
            changed = true;
        }

        if data
            .get("nameToID")
            .and_then(Value::as_object)
            .and_then(|name_to_id| name_to_id.get(name))
            != Some(&json!(sid.clone()))
        {
            object_field_mut(&mut data, "nameToID").insert(name.clone(), json!(sid.clone()));
            changed = true;
        }
        if data
            .get("idToName")
            .and_then(Value::as_object)
            .and_then(|id_to_name| id_to_name.get(&sid))
            != Some(&json!(name))
        {
            object_field_mut(&mut data, "idToName").insert(sid.clone(), json!(name));
            changed = true;
        }

        let ids_file = ids_dir.join(&sid);
        if fs::read_to_string(&ids_file)
            .map(|existing| existing != *name)
            .unwrap_or(true)
        {
            fs::write(&ids_file, name)?;
            set_mode(&ids_file, 0o640)?;
        }
    }

    if changed {
        let tmp_path = secrets_json_path.with_file_name(format!(
            ".{}.tmp.{}",
            secrets_json_path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("secrets.json"),
            process::id()
        ));
        let rendered = serde_json::to_string_pretty(&Value::Object(data))
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?
            + "\n";
        fs::write(&tmp_path, rendered)?;
        set_mode(&tmp_path, 0o600)?;
        fs::rename(&tmp_path, &secrets_json_path)?;
        eprintln!(
            "[proxnix-mount][{vmid}] Podman secrets.json reconciled ({} secret(s))",
            live_names.len()
        );
    }

    Ok(())
}

fn top_level_yaml_keys(path: &Path) -> io::Result<BTreeSet<String>> {
    if !path.exists() {
        return Ok(BTreeSet::new());
    }

    let mut keys = BTreeSet::new();
    for line in fs::read_to_string(path)?.lines() {
        if line.is_empty() || line.starts_with(char::is_whitespace) || !line.contains(':') {
            continue;
        }
        let Some((key, _)) = line.split_once(':') else {
            continue;
        };
        let key = key.trim();
        if !key.is_empty() && key != "sops" {
            keys.insert(key.to_owned());
        }
    }
    Ok(keys)
}

fn podman_secret_id(vmid: &str, name: &str) -> String {
    Uuid::new_v5(
        &Uuid::NAMESPACE_DNS,
        format!("proxnix:{vmid}:{name}").as_bytes(),
    )
    .simple()
    .to_string()
}

fn podman_driver_options() -> Value {
    json!({
        "store": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman store",
        "lookup": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman lookup",
        "list": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman list",
        "delete": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman delete",
    })
}

fn load_json_object(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

fn ensure_object_field(data: &mut Map<String, Value>, field: &str) {
    if !data.get(field).is_some_and(Value::is_object) {
        data.insert(field.to_owned(), Value::Object(Map::new()));
    }
}

fn object_field_mut<'a>(
    data: &'a mut Map<String, Value>,
    field: &str,
) -> &'a mut Map<String, Value> {
    data.get_mut(field)
        .and_then(Value::as_object_mut)
        .expect("object field was initialized")
}

fn set_mode(path: &Path, mode: u32) -> io::Result<()> {
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

fn utc_now_isoformat() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    isoformat_from_unix_parts(now.as_secs() as i64, now.subsec_micros())
}

fn utc_now_seconds_z() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    zulu_seconds_from_unix(now.as_secs() as i64)
}

fn isoformat_from_unix_parts(seconds: i64, micros: u32) -> String {
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00")
}

fn zulu_seconds_from_unix(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = y + if month <= 2 { 1 } else { 0 };
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex;

    static TEMP_COUNTER: AtomicUsize = AtomicUsize::new(0);
    pub(crate) static ENV_LOCK: Mutex<()> = Mutex::new(());

    pub(crate) struct TestTemp {
        path: PathBuf,
    }

    impl TestTemp {
        pub(crate) fn new() -> Self {
            let path = env::temp_dir().join(format!(
                "proxnix-host-test-{}-{}",
                process::id(),
                TEMP_COUNTER.fetch_add(1, Ordering::SeqCst)
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }

        pub(crate) fn path(&self) -> &Path {
            &self.path
        }
    }

    impl Drop for TestTemp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    fn setup_podman_test(tmp: &Path, sops_yaml: Option<&str>) -> (PathBuf, PathBuf) {
        let rootfs = tmp.join("rootfs");
        let secrets_dir = tmp.join("secrets");
        fs::create_dir_all(rootfs.join("etc/secrets")).unwrap();
        fs::create_dir_all(&secrets_dir).unwrap();
        if let Some(sops_yaml) = sops_yaml {
            fs::write(secrets_dir.join("effective.sops.yaml"), sops_yaml).unwrap();
        }
        (rootfs, secrets_dir)
    }

    #[test]
    fn parses_lxc_hook_invocation_arguments() {
        assert_eq!(
            parse_prestart_args(&["101".to_owned(), "lxc".to_owned(), "pre-start".to_owned()])
                .unwrap(),
            ParsedPrestartArgs {
                vmid: Some("101".to_owned()),
                pve_conf: None,
                help: false,
            }
        );
        assert_eq!(
            parse_mount_args(&[
                "--vmid".to_owned(),
                "202".to_owned(),
                "--rootfs".to_owned(),
                "/tmp/rootfs".to_owned(),
            ])
            .unwrap(),
            ParsedMountArgs {
                vmid: Some("202".to_owned()),
                rootfs: Some(PathBuf::from("/tmp/rootfs")),
                help: false,
            }
        );
        assert_eq!(
            parse_poststop_args(&["303".to_owned(), "lxc".to_owned(), "post-stop".to_owned()])
                .unwrap(),
            ParsedPoststopArgs {
                vmid: Some("303".to_owned()),
                help: false,
            }
        );
    }

    #[test]
    fn poststop_hook_entrypoint_removes_staged_state() {
        let _guard = ENV_LOCK.lock().unwrap();
        let tmp = TestTemp::new();
        let run_dir = tmp.path().join("run");
        fs::create_dir_all(run_dir.join("101/bind/runtime")).unwrap();
        env::set_var("PROXNIX_RUN_DIR", &run_dir);
        env::remove_var("LXC_NAME");

        hook_poststop_main(&["101".to_owned(), "lxc".to_owned(), "post-stop".to_owned()]).unwrap();

        assert!(!run_dir.join("101").exists());
        env::remove_var("PROXNIX_RUN_DIR");
    }

    #[test]
    fn extracts_host_root_uid_from_pve_config() {
        let tmp = TestTemp::new();
        let pve_conf = tmp.path().join("101.conf");
        fs::write(&pve_conf, "lxc.idmap: u 0 100000 65536\nunprivileged: 1\n").unwrap();
        assert_eq!(determine_host_root_uid(&pve_conf).unwrap(), "100000");

        fs::write(&pve_conf, "unprivileged: 0\n").unwrap();
        assert_eq!(determine_host_root_uid(&pve_conf).unwrap(), "0");
    }

    #[test]
    fn parses_relay_identity_payload() {
        let payload = "identity: |\n  AGE-SECRET-KEY-1\n  second-line\n";
        assert_eq!(
            parse_identity_payload(payload).unwrap(),
            "AGE-SECRET-KEY-1\nsecond-line\n"
        );
    }

    #[test]
    fn parses_pve_config_fields_used_by_guest_module() {
        let data = parse_pve_conf_content(
            "\
# comment
ostype: nixos
hostname: ct101
nameserver: 1.1.1.1 8.8.8.8
searchdomain: example.test
ssh-public-keys: ssh-ed25519%20AAA%20root%40host%5Cn%23ignored%5Cnssh-rsa%20BBB
[pending]
memory: 2048
",
        );

        assert_eq!(
            data,
            ProxmoxNixData {
                hostname: Some("ct101".to_owned()),
                nameservers: vec!["1.1.1.1".to_owned(), "8.8.8.8".to_owned()],
                search_domain: Some("example.test".to_owned()),
                ssh_keys: vec![
                    "ssh-ed25519 AAA root@host".to_owned(),
                    "ssh-rsa BBB".to_owned()
                ],
            }
        );
    }

    #[test]
    fn renders_the_same_nix_shape_as_the_python_helper() {
        let rendered = generate_proxmox_nix(&ProxmoxNixData {
            hostname: Some("ct${101}".to_owned()),
            nameservers: vec!["1.1.1.1".to_owned(), "8.8.8.8".to_owned()],
            search_domain: Some("example.test".to_owned()),
            ssh_keys: vec!["ssh-ed25519 AAA \"quoted\"".to_owned()],
        });

        assert_eq!(
            rendered,
            "\
# Generated by pve-conf-to-nix.py \u{2014} do not edit by hand.
{ lib, ... }: {
  networking.hostName = lib.mkForce \"ct\\${101}\";
  networking.nameservers = lib.mkForce [ \"1.1.1.1\" \"8.8.8.8\" ];
  networking.search = lib.mkForce [ \"example.test\" ];
  users.users.root.openssh.authorizedKeys.keys = lib.mkForce [ \"ssh-ed25519 AAA \\\"quoted\\\"\" ];
}
"
        );
    }

    #[test]
    fn formats_utc_iso_timestamps_with_python_compatible_offset() {
        assert_eq!(
            isoformat_from_unix_parts(0, 123),
            "1970-01-01T00:00:00.000123+00:00"
        );
        assert_eq!(
            isoformat_from_unix_parts(1_704_067_199, 999_999),
            "2023-12-31T23:59:59.999999+00:00"
        );
        assert_eq!(zulu_seconds_from_unix(0), "1970-01-01T00:00:00Z");
    }

    #[test]
    fn initializes_reconciler_state_schema() {
        let tmp = TestTemp::new();
        let db = tmp.path().join("state/reconciler.sqlite");
        let conn = connect_state_db(&db).unwrap();

        init_state_db(&conn).unwrap();

        let tables = conn
            .prepare("select name from sqlite_master where type = 'table'")
            .unwrap()
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<rusqlite::Result<BTreeSet<_>>>()
            .unwrap();
        assert!(tables.contains("container_observations"));
        assert!(tables.contains("closure_observations"));
        assert!(tables.contains("deployment_attempts"));
    }

    #[test]
    fn updates_container_observation_idempotently() {
        let tmp = TestTemp::new();
        let conn = connect_state_db(&tmp.path().join("reconciler.sqlite")).unwrap();

        observe_container(
            &conn,
            &ContainerObservation {
                vmid: 101,
                node: "pve1".to_owned(),
                desired_system: Some("/nix/store/desired-a".to_owned()),
                current_system: Some("/nix/store/current-a".to_owned()),
                container_is_local: true,
                last_phase: Some("observe".to_owned()),
                last_status: Some("noop-current".to_owned()),
                last_error: None,
            },
        )
        .unwrap();
        observe_container(
            &conn,
            &ContainerObservation {
                vmid: 101,
                node: "pve1".to_owned(),
                desired_system: Some("/nix/store/desired-a".to_owned()),
                current_system: Some("/nix/store/current-a".to_owned()),
                container_is_local: true,
                last_phase: Some("observe".to_owned()),
                last_status: Some("activated".to_owned()),
                last_error: None,
            },
        )
        .unwrap();

        let rows = conn
            .prepare("select vmid, last_status from container_observations")
            .unwrap()
            .query_map([], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        assert_eq!(rows, vec![(101, "activated".to_owned())]);
    }

    #[test]
    fn records_closure_observation_with_explicit_gcroot_state() {
        let tmp = TestTemp::new();
        let conn = connect_state_db(&tmp.path().join("reconciler.sqlite")).unwrap();

        observe_closure(
            &conn,
            &ClosureObservation {
                store_path: "/nix/store/aaa-desired".to_owned(),
                host_has_closure: Some(true),
                container_has_closure: Some(false),
                protected_by_host_gc_root: Some(true),
                gc_root_path: Some("/var/lib/proxnix/gcroots/deploy/aaa-desired".to_owned()),
            },
        )
        .unwrap();

        let row = conn
            .query_row(
                "select store_path, host_has_closure, container_has_closure, protected_by_host_gc_root, gc_root_path from closure_observations",
                [],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, i64>(3)?,
                        row.get::<_, String>(4)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(
            row,
            (
                "/nix/store/aaa-desired".to_owned(),
                1,
                0,
                1,
                "/var/lib/proxnix/gcroots/deploy/aaa-desired".to_owned(),
            )
        );
    }

    #[test]
    fn verifies_real_gcroot_for_closure_observation() {
        let _env_guard = ENV_LOCK.lock().unwrap();
        let tmp = TestTemp::new();
        let root = tmp.path();
        let conn = connect_state_db(&root.join("reconciler.sqlite")).unwrap();
        let fake_bin = root.join("bin");
        fs::create_dir_all(&fake_bin).unwrap();
        let store_path = "/nix/store/aaa-desired";
        let gcroot = root.join("gcroots/deploy/101-desired");
        fs::create_dir_all(gcroot.parent().unwrap()).unwrap();
        symlink(store_path, &gcroot).unwrap();
        let nix_store = fake_bin.join("nix-store");
        fs::write(
            &nix_store,
            format!(
                "\
#!/bin/sh
if [ \"$1\" = \"--query\" ] && [ \"$2\" = \"--roots\" ] && [ \"$3\" = \"{store_path}\" ]; then
  printf '%s\\n' \"{}\"
  exit 0
fi
exit 2
",
                gcroot.display()
            ),
        )
        .unwrap();
        set_mode(&nix_store, 0o755).unwrap();

        let old_path = env::var_os("PATH");
        env::set_var(
            "PATH",
            format!(
                "{}:{}",
                fake_bin.display(),
                old_path
                    .as_deref()
                    .and_then(|value| value.to_str())
                    .unwrap_or("")
            ),
        );
        observe_closure(
            &conn,
            &ClosureObservation {
                store_path: store_path.to_owned(),
                host_has_closure: Some(true),
                container_has_closure: Some(false),
                protected_by_host_gc_root: None,
                gc_root_path: Some(gcroot.display().to_string()),
            },
        )
        .unwrap();
        if let Some(old_path) = old_path {
            env::set_var("PATH", old_path);
        } else {
            env::remove_var("PATH");
        }

        let protected = conn
            .query_row(
                "select protected_by_host_gc_root from closure_observations",
                [],
                |row| row.get::<_, i64>(0),
            )
            .unwrap();
        assert_eq!(protected, 1);
    }

    #[test]
    fn records_deployment_attempts() {
        let tmp = TestTemp::new();
        let conn = connect_state_db(&tmp.path().join("reconciler.sqlite")).unwrap();

        let attempt_id = record_deployment_attempt(
            &conn,
            &DeploymentAttempt {
                vmid: 101,
                store_path: Some("/nix/store/desired-a".to_owned()),
                phase: "seed".to_owned(),
                status: "ok".to_owned(),
                error: None,
                finished_at: Some("2026-01-01T00:00:00Z".to_owned()),
            },
        )
        .unwrap();

        assert_eq!(attempt_id, 1);
        let row = conn
            .query_row(
                "select vmid, store_path, phase, status, finished_at from deployment_attempts",
                [],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, String>(4)?,
                    ))
                },
            )
            .unwrap();
        assert_eq!(
            row,
            (
                101,
                "/nix/store/desired-a".to_owned(),
                "seed".to_owned(),
                "ok".to_owned(),
                "2026-01-01T00:00:00Z".to_owned(),
            )
        );
    }

    #[test]
    fn renders_authority_from_legacy_relay_tree() {
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let pve = tmp.path().join("pve/lxc");
        let authority = root.join("authority");
        fs::create_dir_all(root.join("containers/101/dropins")).unwrap();
        fs::create_dir_all(&pve).unwrap();

        for name in ["base.nix", "common.nix", "security-policy.nix"] {
            fs::write(root.join(name), "{ ... }: {}\n").unwrap();
        }
        fs::write(root.join("site.nix"), "{ ... }: {}\n").unwrap();
        fs::write(root.join("flake.lock"), "{\"nodes\":{\"nixpkgs\":{}}}\n").unwrap();
        fs::write(
            root.join("publish-revision.json"),
            "{\"commit\":\"abc123\",\"branch\":\"main\",\"dirtyWorktreeIgnored\":false}\n",
        )
        .unwrap();
        fs::write(
            root.join("containers/101/dropins/workload.nix"),
            "{ ... }: {}\n",
        )
        .unwrap();
        fs::write(
            pve.join("101.conf"),
            [
                "ostype: nixos",
                "hostname: ct101",
                "memory: 2048",
                "swap: 512",
                "cores: 2",
                "rootfs: local-lvm:vm-101-disk-0,size=8G",
                "net0: name=eth0,bridge=vmbr0,ip=dhcp",
                "unprivileged: 1",
                "features: nesting=1,keyctl=1",
                "",
            ]
            .join("\n"),
        )
        .unwrap();

        let manifests = render_authority(&root, &authority, &pve, "pve1").unwrap();

        assert_eq!(
            manifests
                .iter()
                .map(|manifest| manifest.vmid.as_str())
                .collect::<Vec<_>>(),
            vec!["101"]
        );
        assert!(authority.join("flake.nix").is_file());
        assert_eq!(
            fs::read_to_string(authority.join("flake.lock")).unwrap(),
            "{\"nodes\":{\"nixpkgs\":{}}}\n"
        );
        assert!(authority.join("modules/proxnix-guest-base.nix").is_file());
        assert!(authority.join("generated/legacy/site.nix").is_file());
        assert!(authority
            .join("generated/containers/101/proxmox.nix")
            .is_file());

        let modules_nix =
            fs::read_to_string(authority.join("generated/containers/101/modules.nix")).unwrap();
        assert!(modules_nix.contains("../../../modules/proxnix-guest-base.nix"));
        assert!(modules_nix.contains("../../legacy/site.nix"));
        assert!(modules_nix.contains("./dropins/workload.nix"));

        let manifest_nix =
            fs::read_to_string(authority.join("generated/node-manifest.nix")).unwrap();
        assert!(manifest_nix.contains("nodeName = \"pve1\";"));
        assert!(manifest_nix.contains("\"101\" = {"));
        assert!(manifest_nix
            .contains("systemAttr = \"nixosConfigurations.ct101.config.system.build.toplevel\";"));
        assert!(manifest_nix.contains("hostname = \"ct101\";"));
        assert!(manifest_nix.contains("memory = 2048;"));
        assert!(manifest_nix.contains("unprivileged = true;"));
        assert!(manifest_nix.contains("localVmids = ["));
        assert!(manifest_nix.contains("local = false;"));
        assert!(manifest_nix.contains("observedPveConfig = true;"));

        let flake_nix = fs::read_to_string(authority.join("flake.nix")).unwrap();
        assert!(flake_nix.contains("proxnix-golden-template = lib.nixosSystem"));
        assert!(flake_nix.contains("./modules/proxnix-guest-base.nix"));
        assert!(flake_nix.contains("proxnix.containers = manifest.containers;"));
    }

    #[test]
    fn keeps_cluster_container_without_matching_local_pve_config() {
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let authority = root.join("authority");
        fs::create_dir_all(root.join("containers/101")).unwrap();
        for name in ["base.nix", "common.nix", "security-policy.nix"] {
            fs::write(root.join(name), "{ ... }: {}\n").unwrap();
        }

        let manifests =
            render_authority(&root, &authority, &tmp.path().join("missing-pve"), "pve1").unwrap();

        assert_eq!(
            manifests
                .iter()
                .map(|manifest| manifest.vmid.as_str())
                .collect::<Vec<_>>(),
            vec!["101"]
        );
        let manifest_nix =
            fs::read_to_string(authority.join("generated/node-manifest.nix")).unwrap();
        assert!(manifest_nix.contains("vmids = ["));
        assert!(manifest_nix.contains("containers = {"));
        assert!(manifest_nix.contains("\"101\" = {"));
        assert!(manifest_nix.contains("local = false;"));
        assert!(manifest_nix.contains("observedPveConfig = false;"));
    }

    #[test]
    fn preserves_authority_flake_lock_when_site_lock_is_absent() {
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let authority = root.join("authority");
        fs::create_dir_all(root.join("containers/101")).unwrap();
        fs::create_dir_all(&authority).unwrap();
        fs::write(authority.join("flake.lock"), "{\"stale\":true}\n").unwrap();
        for name in ["base.nix", "common.nix", "security-policy.nix"] {
            fs::write(root.join(name), "{ ... }: {}\n").unwrap();
        }

        render_authority(&root, &authority, &tmp.path().join("missing-pve"), "pve1").unwrap();

        assert_eq!(
            fs::read_to_string(authority.join("flake.lock")).unwrap(),
            "{\"stale\":true}\n"
        );
    }

    #[test]
    fn reconciles_podman_secrets_json_for_live_keys() {
        let tmp = TestTemp::new();
        let (rootfs, secrets_dir) = setup_podman_test(
            tmp.path(),
            Some("alpha: ENC[a]\nbeta: ENC[b]\nsops:\n    version: 3\n"),
        );

        reconcile_podman_secrets(&rootfs, "101", &secrets_dir).unwrap();

        let secrets_json = rootfs.join("var/lib/containers/storage/secrets/secrets.json");
        assert!(secrets_json.exists());
        let data: Value = serde_json::from_str(&fs::read_to_string(secrets_json).unwrap()).unwrap();
        let secrets = data["secrets"].as_object().unwrap();
        let names = secrets
            .values()
            .map(|entry| entry["name"].as_str().unwrap().to_owned())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            names,
            BTreeSet::from(["alpha".to_owned(), "beta".to_owned()])
        );
        for entry in secrets.values() {
            assert_eq!(entry["labels"], json!({ "proxnix.managed": "true" }));
            assert_eq!(entry["driver"], "shell");
            assert_eq!(entry["driverOptions"], podman_driver_options());
        }

        let ids_dir = rootfs.join("etc/secrets/.ids");
        let ids = fs::read_dir(ids_dir)
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(ids, BTreeSet::from(["alpha".to_owned(), "beta".to_owned()]));
    }

    #[test]
    fn reconciles_podman_secrets_json_by_removing_stale_managed_entries() {
        let tmp = TestTemp::new();
        let (rootfs, secrets_dir) =
            setup_podman_test(tmp.path(), Some("alpha: ENC[a]\nsops:\n    version: 3\n"));

        reconcile_podman_secrets(&rootfs, "101", &secrets_dir).unwrap();
        fs::write(
            secrets_dir.join("effective.sops.yaml"),
            "beta: ENC[b]\nsops:\n    version: 3\n",
        )
        .unwrap();
        reconcile_podman_secrets(&rootfs, "101", &secrets_dir).unwrap();

        let data: Value = serde_json::from_str(
            &fs::read_to_string(rootfs.join("var/lib/containers/storage/secrets/secrets.json"))
                .unwrap(),
        )
        .unwrap();
        let names = data["secrets"]
            .as_object()
            .unwrap()
            .values()
            .map(|entry| entry["name"].as_str().unwrap().to_owned())
            .collect::<BTreeSet<_>>();
        assert_eq!(names, BTreeSet::from(["beta".to_owned()]));

        let ids = fs::read_dir(rootfs.join("etc/secrets/.ids"))
            .unwrap()
            .map(|entry| fs::read_to_string(entry.unwrap().path()).unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(ids, BTreeSet::from(["beta".to_owned()]));
    }

    #[test]
    fn reconciles_podman_secrets_json_without_touching_unmanaged_entries() {
        let tmp = TestTemp::new();
        let (rootfs, secrets_dir) =
            setup_podman_test(tmp.path(), Some("alpha: ENC[a]\nsops:\n    version: 3\n"));
        let secrets_json = rootfs.join("var/lib/containers/storage/secrets/secrets.json");
        fs::create_dir_all(secrets_json.parent().unwrap()).unwrap();
        fs::write(
            &secrets_json,
            serde_json::to_string(&json!({
                "secrets": {
                    "deadbeef": {
                        "name": "manual",
                        "id": "deadbeef",
                        "labels": { "foo": "bar" },
                        "driver": "file",
                    }
                },
                "nameToID": { "manual": "deadbeef" },
                "idToName": { "deadbeef": "manual" },
            }))
            .unwrap(),
        )
        .unwrap();

        reconcile_podman_secrets(&rootfs, "101", &secrets_dir).unwrap();

        let data: Value = serde_json::from_str(&fs::read_to_string(secrets_json).unwrap()).unwrap();
        assert!(data["secrets"]
            .as_object()
            .unwrap()
            .contains_key("deadbeef"));
        let names = data["secrets"]
            .as_object()
            .unwrap()
            .values()
            .map(|entry| entry["name"].as_str().unwrap().to_owned())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            names,
            BTreeSet::from(["alpha".to_owned(), "manual".to_owned()])
        );
    }
}
