use std::env;
use std::fs::{self, File};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{chown, symlink, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::authority::render_authority;
use crate::common::{
    default_node_name, env_bool, env_path, find_in_path, parse_pct_mount_rootfs,
    remove_file_if_exists, remove_path_if_exists, require_nix, require_nix_store, require_pct,
    require_socat, rootfs_path, set_mode, shell_quote, take_arg, utc_now_seconds_z, valid_vmid,
    HostError, HostResult, DEFAULT_PROXNIX_DIR, DEFAULT_PVE_LXC_DIR, GUEST_PROXNIX_DIR,
};
use crate::create_lxc;
use crate::payload_stage::{self, StagedPayload};
use crate::pve_conf::pve_conf_has_tag;

#[derive(Debug, PartialEq, Eq)]
struct BuildGoldenOptions {
    node_name: String,
}

#[derive(Debug)]
struct BuildGoldenConfig {
    root: PathBuf,
    authority: PathBuf,
    pve_lxc_dir: PathBuf,
    gcroot_dir: PathBuf,
    authority_render: Option<PathBuf>,
}

#[derive(Debug, PartialEq, Eq)]
struct SeedOfflineOptions {
    vmid: String,
    rootfs: PathBuf,
}

#[derive(Debug)]
struct SeedOfflineConfig {
    status_dir: PathBuf,
}

#[derive(Debug, PartialEq, Eq)]
struct SeedOptions {
    vmid: String,
    rootfs: Option<PathBuf>,
    passthrough_args: Vec<String>,
    help: bool,
}

#[derive(Debug, PartialEq, Eq)]
struct StartHostOptions {
    vmid: String,
    rootfs: PathBuf,
    help: bool,
}

#[derive(Debug, PartialEq, Eq)]
enum SeedAction {
    Help,
    Offline(SeedOfflineOptions),
    Running(Vec<String>),
}

pub(crate) fn build_golden_main(args: &[String]) -> HostResult<()> {
    let options = parse_build_golden_args(args)?;
    let config = BuildGoldenConfig::from_env();
    build_golden(&config, &options)
        .map_err(|err| HostError::new(format!("build-golden failed: {err}")))
}

pub(crate) fn seed_offline_main(args: &[String]) -> HostResult<()> {
    let options = parse_seed_offline_args(args)?;
    let root = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
    let status_dir = env::var_os("PROXNIX_STATUS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("status"));
    seed_offline(&SeedOfflineConfig { status_dir }, &options)
        .map_err(|err| HostError::new(format!("seed-offline failed: {err}")))
}

pub(crate) fn seed_main(args: &[String]) -> HostResult<()> {
    match seed_action(args)? {
        SeedAction::Help => {
            seed_usage();
            Ok(())
        }
        SeedAction::Offline(options) => {
            let root = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
            let status_dir = env::var_os("PROXNIX_STATUS_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|| root.join("status"));
            seed_offline(&SeedOfflineConfig { status_dir }, &options)
                .map_err(|err| HostError::new(format!("seed failed: {err}")))
        }
        SeedAction::Running(passthrough_args) => {
            let mut phase_args = vec!["--seed-only".to_owned()];
            phase_args.extend(passthrough_args);
            main(&phase_args)
        }
    }
}

pub(crate) fn build_main(args: &[String]) -> HostResult<()> {
    let mut phase_args = vec!["--build-only".to_owned()];
    phase_args.extend(args.iter().cloned());
    main(&phase_args)
}

pub(crate) fn activate_main(args: &[String]) -> HostResult<()> {
    let mut phase_args = vec!["--activate-only".to_owned()];
    phase_args.extend(args.iter().cloned());
    main(&phase_args)
}

pub(crate) fn rollback_main(args: &[String]) -> HostResult<()> {
    let options = parse_rollback_args(args)?;
    let config = ReconcileConfig::from_env(options.node_name);
    rollback_container(&config, &options.vmid, options.start_stopped)
}

pub(crate) fn start_main(args: &[String]) -> HostResult<()> {
    let mut phase_args = args.to_vec();
    phase_args.push("--start-stopped".to_owned());
    main(&phase_args)
}

pub(crate) fn start_host_main(args: &[String]) -> HostResult<()> {
    let options = parse_start_host_args(args)?;
    if options.help {
        start_host_usage();
        return Ok(());
    }
    let config = ReconcileConfig::from_env(
        env::var("PROXNIX_NODE_NAME").unwrap_or_else(|_| default_node_name()),
    );
    start_host_seed(&config, &options)
        .map_err(|err| HostError::new(format!("start-host hook failed: {err}")))
}

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let options = parse_reconcile_args(args)?;
    if options.help {
        reconcile_usage();
        return Ok(());
    }
    let config = ReconcileConfig::from_env(options.node_name.clone());
    match options.mode {
        ReconcileMode::Status => status_main(&config, options.vmid.as_deref()),
        ReconcileMode::DryRun => dry_run(&config, options.vmid.as_deref()),
        ReconcileMode::BuildOnly => build_only(&config, options.vmid.as_deref()),
        ReconcileMode::SeedOnly => seed_only(&config, options.vmid.as_deref()),
        ReconcileMode::ActivateOnly => {
            activate_only(&config, options.vmid.as_deref(), options.start_stopped)
        }
        ReconcileMode::AutoTag => reconcile_auto_tag(&config, options.force),
        ReconcileMode::Full => {
            if let Some(vmid) = options.vmid.as_deref() {
                reconcile_selected(
                    &config,
                    vmid,
                    options.recreate_missing,
                    options.start_stopped,
                    options.online,
                    options.force,
                )
            } else if options.all_ct {
                reconcile_all_running(
                    &config,
                    options.recreate_missing,
                    options.start_stopped,
                    options.online,
                    options.force,
                )
            } else {
                Err("full reconcile requires --vmid <id> or --all-ct".into())
            }
        }
    }
}

#[derive(Debug)]
struct ReconcileConfig {
    root: PathBuf,
    authority: PathBuf,
    status_dir: PathBuf,
    pve_lxc_dir: PathBuf,
    run_dir: PathBuf,
    gcroot_dir: PathBuf,
    node_name: String,
    authority_render: Option<PathBuf>,
    create_lxc: Option<PathBuf>,
    container_nix_daemon_socket: String,
    bridge_ready_timeout: u64,
    nix_command_timeout: u64,
}

#[derive(Debug, PartialEq, Eq)]
enum ReconcileMode {
    Full,
    AutoTag,
    DryRun,
    BuildOnly,
    SeedOnly,
    ActivateOnly,
    Status,
}

#[derive(Debug)]
struct ReconcileOptions {
    mode: ReconcileMode,
    vmid: Option<String>,
    node_name: String,
    recreate_missing: bool,
    start_stopped: bool,
    online: bool,
    all_ct: bool,
    force: bool,
    help: bool,
}

#[derive(Debug)]
struct RollbackOptions {
    vmid: String,
    node_name: String,
    start_stopped: bool,
}

struct LockGuard {
    _file: File,
}

#[cfg(unix)]
unsafe extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}

struct ProxnixPaths {
    root: PathBuf,
    authority: PathBuf,
    pve_lxc_dir: PathBuf,
    gcroot_dir: PathBuf,
    authority_render: Option<PathBuf>,
}

fn proxnix_paths_from_env() -> ProxnixPaths {
    let root = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
    let authority = env::var_os("PROXNIX_AUTHORITY_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("authority"));
    let pve_lxc_dir = env_path("PROXNIX_PVE_LXC_DIR", DEFAULT_PVE_LXC_DIR);
    let gcroot_dir = env::var_os("PROXNIX_GCROOT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("gcroots/deploy"));
    let authority_render = env::var_os("PROXNIX_AUTHORITY_RENDER").map(PathBuf::from);
    ProxnixPaths {
        root,
        authority,
        pve_lxc_dir,
        gcroot_dir,
        authority_render,
    }
}

impl ReconcileConfig {
    fn from_env(node_name: String) -> Self {
        let ProxnixPaths {
            root,
            authority,
            pve_lxc_dir,
            gcroot_dir,
            authority_render,
        } = proxnix_paths_from_env();
        let status_dir = env::var_os("PROXNIX_STATUS_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("status"));
        let run_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
        Self {
            root,
            authority,
            status_dir,
            pve_lxc_dir,
            run_dir,
            gcroot_dir,
            node_name,
            authority_render,
            create_lxc: env::var_os("PROXNIX_CREATE_LXC").map(PathBuf::from),
            container_nix_daemon_socket: env::var("PROXNIX_CONTAINER_NIX_DAEMON_SOCKET")
                .unwrap_or_else(|_| "/nix/var/nix/daemon-socket/socket".to_owned()),
            bridge_ready_timeout: env::var("PROXNIX_CONTAINER_NIX_DAEMON_BRIDGE_READY_TIMEOUT")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(15),
            nix_command_timeout: env::var("PROXNIX_CONTAINER_NIX_COMMAND_TIMEOUT")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(0),
        }
    }
}

fn parse_reconcile_args(args: &[String]) -> Result<ReconcileOptions, String> {
    let mut mode = ReconcileMode::Full;
    let mut vmid = None;
    let mut node_name = env::var("PROXNIX_NODE_NAME").unwrap_or_else(|_| default_node_name());
    let mut recreate_missing = false;
    let mut start_stopped = env_bool("PROXNIX_RECONCILE_START_STOPPED");
    let mut online = env_bool("PROXNIX_RECONCILE_ONLINE");
    let mut all_ct = false;
    let mut force = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--dry-run" => mode = ReconcileMode::DryRun,
            "--auto-tag" => mode = ReconcileMode::AutoTag,
            "--build-only" => mode = ReconcileMode::BuildOnly,
            "--seed-only" => mode = ReconcileMode::SeedOnly,
            "--activate-only" => mode = ReconcileMode::ActivateOnly,
            "--status" => mode = ReconcileMode::Status,
            "--recreate-missing" => recreate_missing = true,
            "--start-stopped" => start_stopped = true,
            "--online" => online = true,
            "--all-ct" => all_ct = true,
            "--force" => force = true,
            "--vmid" => {
                let value = take_arg(args, &mut index, "--vmid")?;
                if !valid_vmid(&value) {
                    return Err(format!("invalid VMID: {value}"));
                }
                vmid = Some(value);
            }
            "--node-name" => node_name = take_arg(args, &mut index, "--node-name")?,
            "-h" | "--help" => {
                return Ok(ReconcileOptions {
                    mode,
                    vmid,
                    node_name,
                    recreate_missing,
                    start_stopped,
                    online,
                    all_ct,
                    force,
                    help: true,
                });
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        index += 1;
    }
    if matches!(mode, ReconcileMode::Full | ReconcileMode::AutoTag) && vmid.is_some() && all_ct {
        return Err("reconcile accepts either --vmid <id> or --all-ct, not both".to_owned());
    }
    if matches!(mode, ReconcileMode::AutoTag) && (vmid.is_some() || all_ct) {
        return Err("--auto-tag does not accept --vmid or --all-ct".to_owned());
    }
    Ok(ReconcileOptions {
        mode,
        vmid,
        node_name,
        recreate_missing,
        start_stopped,
        online,
        all_ct,
        force,
        help: false,
    })
}

fn parse_rollback_args(args: &[String]) -> Result<RollbackOptions, String> {
    let mut vmid = None;
    let mut node_name = env::var("PROXNIX_NODE_NAME").unwrap_or_else(|_| default_node_name());
    let mut start_stopped = env_bool("PROXNIX_RECONCILE_START_STOPPED");
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => {
                let value = take_arg(args, &mut index, "--vmid")?;
                if !valid_vmid(&value) {
                    return Err(format!("invalid VMID: {value}"));
                }
                vmid = Some(value);
            }
            "--node-name" => node_name = take_arg(args, &mut index, "--node-name")?,
            "--start-stopped" => start_stopped = true,
            other => return Err(format!("unknown rollback argument: {other}")),
        }
        index += 1;
    }
    Ok(RollbackOptions {
        vmid: vmid.ok_or("--vmid is required")?,
        node_name,
        start_stopped,
    })
}

fn reconcile_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host reconcile [--dry-run] [--online] --vmid <id> [--node-name <name>]
  proxnix-host reconcile [--dry-run] [--online] --all-ct [--node-name <name>]
  proxnix-host reconcile --auto-tag [--node-name <name>]
  proxnix-host reconcile --build-only --vmid <id> [--node-name <name>]
  proxnix-host reconcile --seed-only --vmid <id>
  proxnix-host reconcile --activate-only --vmid <id>
  proxnix-host reconcile --vmid <id> --recreate-missing
  proxnix-host reconcile --status [--vmid <id>]
  proxnix-host reconcile --vmid <id> --start-stopped
  proxnix-host reconcile --vmid <id> --force

Default reconcile builds on the host and seeds stopped CT rootfs state without
starting stopped CTs. Use proxnix-host start --vmid <id> when the stopped CT
should be started after offline seed. Use --online to seed and activate through
the running guest Nix daemon bridge.
"
    );
}

fn take_global_lock(config: &ReconcileConfig) -> Result<LockGuard, String> {
    take_lock(
        &config.run_dir.join("reconcile.lock"),
        "another proxnix-host mutation run is active",
    )
}

fn take_container_lock(config: &ReconcileConfig, vmid: &str) -> Result<LockGuard, String> {
    take_lock(
        &config.run_dir.join(format!("reconcile-{vmid}.lock")),
        &format!("another proxnix-host reconcile run is active for VMID {vmid}"),
    )
}

fn take_lock(path: &Path, busy_message: &str) -> Result<LockGuard, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("failed to create lock dir: {err}"))?;
    }
    let file = File::create(path).map_err(|err| format!("failed to open lock: {err}"))?;
    let rc = unsafe { flock(file.as_raw_fd(), 2 | 4) };
    if rc == 0 {
        Ok(LockGuard { _file: file })
    } else {
        Err(busy_message.to_owned())
    }
}

fn render_reconcile_authority(config: &ReconcileConfig) -> Result<(), String> {
    if let Some(authority_render) = &config.authority_render {
        let status = Command::new(authority_render)
            .arg("--root")
            .arg(&config.root)
            .arg("--authority")
            .arg(&config.authority)
            .arg("--pve-lxc-dir")
            .arg(&config.pve_lxc_dir)
            .arg("--node-name")
            .arg(&config.node_name)
            .stdout(Stdio::null())
            .status()
            .map_err(|err| format!("failed to run PROXNIX_AUTHORITY_RENDER: {err}"))?;
        if status.success() {
            return Ok(());
        }
        return Err("PROXNIX_AUTHORITY_RENDER failed".to_owned());
    }
    render_authority(
        &config.root,
        &config.authority,
        &config.pve_lxc_dir,
        &config.node_name,
    )
    .map(|_| ())
    .map_err(|err| format!("authority render failed: {err}"))
}

fn node_attr_fragment(node_name: &str) -> String {
    let mut chars = node_name.chars();
    let valid_first = chars
        .next()
        .map(|ch| ch == '_' || ch.is_ascii_alphabetic())
        .unwrap_or(false);
    let valid_rest =
        chars.all(|ch| ch == '_' || ch == '\'' || ch == '-' || ch.is_ascii_alphanumeric());
    if valid_first && valid_rest {
        format!("proxnix.nodes.{node_name}")
    } else {
        format!("proxnix.nodes.\"{}\"", node_name.replace('"', "\\\""))
    }
}

fn eval_node_manifest(config: &ReconcileConfig) -> Result<Value, String> {
    eval_nix_attr_json(
        config,
        &node_attr_fragment(&config.node_name),
        &format!("node manifest for {}", config.node_name),
    )
}

fn container_attr_fragment(vmid: &str) -> String {
    format!("proxnix.containers.\"{}\"", vmid.replace('"', "\\\""))
}

fn eval_manifest_container(config: &ReconcileConfig, vmid: &str) -> Result<Value, String> {
    let value = eval_nix_attr_json(
        config,
        &container_attr_fragment(vmid),
        &format!("manifest for VMID {vmid}"),
    )?;
    if let Some(containers) = manifest_containers(&value) {
        if let Some(container) = containers.get(vmid) {
            return Ok(container.clone());
        }
        return Err(format!(
            "VMID {vmid} is not present in proxnix.nodes.{}",
            config.node_name
        ));
    }
    if value.is_object() {
        return Ok(value);
    }
    Err(format!(
        "VMID {vmid} is not present in proxnix.nodes.{}",
        config.node_name
    ))
}

fn eval_nix_attr_json(
    config: &ReconcileConfig,
    attr_fragment: &str,
    subject: &str,
) -> Result<Value, String> {
    let nix = require_nix()?;
    let output = Command::new(nix)
        .arg("eval")
        .arg("--json")
        .arg(format!("{}#{attr_fragment}", config.authority.display()))
        .output()
        .map_err(|err| format!("failed to run nix eval: {err}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        if !stderr.is_empty() {
            return Err(format!("failed to evaluate {subject}: {stderr}"));
        }
        return Err(format!("failed to evaluate {subject}"));
    }
    serde_json::from_slice(&output.stdout).map_err(|err| format!("invalid manifest JSON: {err}"))
}

fn manifest_containers(manifest: &Value) -> Option<&Map<String, Value>> {
    manifest.get("containers")?.as_object()
}

fn manifest_container<'a>(manifest: &'a Value, vmid: &str) -> Result<&'a Value, String> {
    manifest_containers(manifest)
        .and_then(|containers| containers.get(vmid))
        .ok_or_else(|| format!("VMID {vmid} is not present in manifest"))
}

fn manifest_string(container: &Value, key: &str) -> String {
    container
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn manifest_hostname(container: &Value, vmid: &str) -> String {
    container
        .get("hostname")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .unwrap_or_else(|| format!("ct{vmid}"))
}

fn manifest_source_revision(container: &Value) -> Value {
    container
        .get("sourceRevision")
        .cloned()
        .unwrap_or(Value::Null)
}

fn pve_field(container: &Value, field: &str) -> String {
    let Some(value) = container.get("pve").and_then(|pve| pve.get(field)) else {
        return String::new();
    };
    match value {
        Value::String(value) => value.clone(),
        Value::Bool(value) => bool_int_string(*value),
        Value::Number(value) => value.to_string(),
        _ => String::new(),
    }
}

fn bool_int_string(value: bool) -> String {
    if value {
        "1".to_owned()
    } else {
        "0".to_owned()
    }
}

fn placement_field(container: &Value, field: &str) -> String {
    container
        .get("placement")
        .and_then(|placement| placement.get(field))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn placement_bool(container: &Value, field: &str) -> bool {
    container
        .get("placement")
        .and_then(|placement| placement.get(field))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn manifest_declares_local(node_name: &str, container: &Value) -> bool {
    placement_bool(container, "local")
        || placement_field(container, "node") == node_name
        || placement_field(container, "targetNode") == node_name
}

fn sorted_vmids(manifest: &Value) -> Vec<String> {
    let mut vmids: Vec<String> = manifest_containers(manifest)
        .map(|containers| containers.keys().cloned().collect())
        .unwrap_or_default();
    vmids.sort_by_key(|vmid| vmid.parse::<u64>().unwrap_or(u64::MAX));
    vmids
}

fn cluster_placement_node(vmid: &str) -> Option<String> {
    let pvesh = find_in_path("pvesh")?;
    let output = Command::new(pvesh)
        .arg("get")
        .arg("/cluster/resources")
        .arg("--type")
        .arg("vm")
        .arg("--output-format")
        .arg("json")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let resources: Value = serde_json::from_slice(&output.stdout).ok()?;
    resources.as_array()?.iter().find_map(|resource| {
        let resource_vmid = resource.get("vmid")?;
        let matches = resource_vmid
            .as_u64()
            .map(|value| value.to_string() == vmid)
            .unwrap_or_else(|| resource_vmid.as_str() == Some(vmid));
        matches.then(|| resource.get("node")?.as_str().map(str::to_owned))?
    })
}

fn is_local_container(config: &ReconcileConfig, vmid: &str) -> bool {
    if let Some(node) = cluster_placement_node(vmid) {
        return node == config.node_name;
    }
    let Some(pct) = find_in_path("pct") else {
        return false;
    };
    Command::new(pct)
        .arg("status")
        .arg(vmid)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn container_state(vmid: &str) -> String {
    let Some(pct) = find_in_path("pct") else {
        return String::new();
    };
    let Ok(output) = Command::new(pct).arg("status").arg(vmid).output() else {
        return String::new();
    };
    if !output.status.success() {
        return String::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .split_whitespace()
        .nth(1)
        .unwrap_or_default()
        .to_owned()
}

fn selected_container_is_local_or_skip(config: &ReconcileConfig, vmid: &str) -> bool {
    if is_local_container(config, vmid) {
        true
    } else {
        println!("{vmid} skip not-local");
        false
    }
}

fn manifest_container_is_local_or_planned(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
) -> bool {
    is_local_container(config, vmid) || manifest_declares_local(&config.node_name, container)
}

fn manifest_container_is_local_or_skip(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
) -> bool {
    if manifest_container_is_local_or_planned(config, vmid, container) {
        true
    } else {
        println!("{vmid} skip not-local");
        false
    }
}

fn reject_held_container_unless_forced(
    config: &ReconcileConfig,
    vmid: &str,
    force: bool,
) -> HostResult<()> {
    if force {
        return Ok(());
    }
    let pve_conf = config.pve_lxc_dir.join(format!("{vmid}.conf"));
    if pve_conf_has_tag(&pve_conf, "nix-hold").unwrap_or(false) {
        return Err(HostError::new(format!(
            "VMID {vmid} has Proxmox tag nix-hold; remove the tag or pass --force"
        )));
    }
    Ok(())
}

fn status_path(config: &ReconcileConfig, vmid: &str) -> PathBuf {
    config.status_dir.join(format!("{vmid}.json"))
}

fn read_optional_status(config: &ReconcileConfig, vmid: &str) -> Map<String, Value> {
    read_status_object(&status_path(config, vmid)).unwrap_or_default()
}

fn status_main(config: &ReconcileConfig, selected_vmid: Option<&str>) -> HostResult<()> {
    if let Some(vmid) = selected_vmid {
        let path = status_path(config, vmid);
        if !path.is_file() {
            return Err(format!("status not found for VMID {vmid}: {}", path.display()).into());
        }
        println!(
            "{}",
            fs::read_to_string(path)
                .map_err(|err| err.to_string())?
                .trim_end()
        );
        return Ok(());
    }
    if !config.status_dir.is_dir() {
        println!("[]");
        return Ok(());
    }
    let mut values = Vec::new();
    let entries = fs::read_dir(&config.status_dir).map_err(|err| err.to_string())?;
    for entry in entries {
        let path = entry.map_err(|err| err.to_string())?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            continue;
        }
        if let Ok(value) = fs::read_to_string(&path)
            .ok()
            .and_then(|content| serde_json::from_str::<Value>(&content).ok())
            .ok_or(())
        {
            values.push(value);
        }
    }
    println!(
        "{}",
        serde_json::to_string(&Value::Array(values)).map_err(|err| err.to_string())?
    );
    Ok(())
}

fn dry_run(config: &ReconcileConfig, selected_vmid: Option<&str>) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    render_reconcile_authority(config)?;
    let manifest = if selected_vmid.is_some() {
        None
    } else {
        Some(eval_node_manifest(config)?)
    };
    let selected_container = selected_vmid
        .map(|vmid| eval_manifest_container(config, vmid))
        .transpose()?;
    let vmids = selected_vmid
        .map(|vmid| vec![vmid.to_owned()])
        .unwrap_or_else(|| sorted_vmids(manifest.as_ref().expect("manifest is loaded")));
    if vmids.is_empty() {
        println!(
            "no managed containers in proxnix.nodes.{}",
            config.node_name
        );
        return Ok(());
    }
    for vmid in vmids {
        let container = if selected_vmid.is_some() {
            selected_container
                .as_ref()
                .expect("selected container is loaded")
                .clone()
        } else {
            manifest_container(manifest.as_ref().expect("manifest is loaded"), &vmid)?.clone()
        };
        if !manifest_container_is_local_or_skip(config, &vmid, &container) {
            continue;
        }
        let desired_system = manifest_string(&container, "system");
        let status = read_optional_status(config, &vmid);
        let last_desired = status_desired_system(&status);
        let current_system = status_current_system(&status);
        if last_desired == desired_system
            && !current_system.is_empty()
            && current_system == desired_system
        {
            println!("{vmid} noop current system matches desired");
            continue;
        }
        println!(
            "{} build {}",
            vmid,
            if desired_system.is_empty() {
                "unknown until build"
            } else {
                &desired_system
            }
        );
        println!("{vmid} keep local CT");
        println!("{vmid} seed desired closure");
        println!("{vmid} activate desired system");
    }
    Ok(())
}

fn build_manifest_system(
    config: &ReconcileConfig,
    selected_vmid: &str,
    container: &Value,
) -> Result<String, String> {
    let system_attr = manifest_string(container, "systemAttr");
    if system_attr.is_empty() {
        return Err(format!(
            "manifest for VMID {selected_vmid} is missing systemAttr"
        ));
    }
    fs::create_dir_all(&config.gcroot_dir).map_err(|err| err.to_string())?;
    let root_path = config.gcroot_dir.join(format!("{selected_vmid}-desired"));
    let nix = require_nix()?;
    let output = Command::new(nix)
        .arg("build")
        .arg("--out-link")
        .arg(&root_path)
        .arg("--print-out-paths")
        .arg(format!("{}#{system_attr}", config.authority.display()))
        .output()
        .map_err(|err| format!("failed to run nix build: {err}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_owned());
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .replace(['\r', '\n'], "")
        .trim()
        .to_owned())
}

fn protect_host_closure(config: &ReconcileConfig, vmid: &str, system_path: &str) -> bool {
    let Some(nix_store) = find_in_path("nix-store") else {
        return false;
    };
    let _ = fs::create_dir_all(&config.gcroot_dir);
    let root_path = config.gcroot_dir.join(format!("{vmid}-desired"));
    Command::new(nix_store)
        .arg("--realise")
        .arg("--add-root")
        .arg(root_path)
        .arg("--indirect")
        .arg(system_path)
        .stdout(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn gcroot_present(config: &ReconcileConfig, vmid: &str, system_path: &str) -> bool {
    let root_path = config.gcroot_dir.join(format!("{vmid}-desired"));
    if !fs::symlink_metadata(&root_path)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        return false;
    }
    if fs::read_link(&root_path)
        .map(|target| target != Path::new(system_path))
        .unwrap_or(true)
    {
        return false;
    }
    let Some(nix_store) = find_in_path("nix-store") else {
        return false;
    };
    let Ok(output) = Command::new(nix_store)
        .arg("--query")
        .arg("--roots")
        .arg(system_path)
        .output()
    else {
        return false;
    };
    output.status.success()
        && String::from_utf8_lossy(&output.stdout)
            .lines()
            .any(|line| line == root_path.display().to_string())
}

fn nullable_string(value: &str) -> Value {
    if value.is_empty() {
        Value::Null
    } else {
        json!(value)
    }
}

struct DeployStatusContext<'a> {
    vmid: &'a str,
    hostname: &'a str,
    source_revision: &'a Value,
    desired_system: &'a str,
    current_system: &'a str,
    previous_system: &'a str,
}

struct DeployStatusOutcome<'a> {
    host_has_closure: bool,
    container_has_closure: bool,
    protected: bool,
    last_build_status: &'a str,
    last_deploy_status: &'a str,
    last_error: Value,
}

fn deploy_status_value(
    config: &ReconcileConfig,
    context: &DeployStatusContext<'_>,
    outcome: DeployStatusOutcome<'_>,
) -> Result<Value, String> {
    let vmid = context
        .vmid
        .parse::<u64>()
        .map_err(|err| format!("invalid VMID {}: {err}", context.vmid))?;
    Ok(json!({
        "vmid": vmid,
        "hostname": context.hostname,
        "node": config.node_name,
        "local": true,
        "sourceRevision": context.source_revision,
        "desired_system": context.desired_system,
        "current_system": nullable_string(context.current_system),
        "previous_system": nullable_string(context.previous_system),
        "container_is_local": true,
        "host_has_closure": outcome.host_has_closure,
        "container_has_closure": outcome.container_has_closure,
        "protected_by_host_gc_root": outcome.protected,
        "lastBuildStatus": outcome.last_build_status,
        "lastDeployStatus": outcome.last_deploy_status,
        "lastError": outcome.last_error,
        "updatedAt": utc_now_seconds_z(),
    }))
}

fn write_build_status(
    config: &ReconcileConfig,
    context: &DeployStatusContext<'_>,
    last_deploy_status: &str,
) -> Result<(), String> {
    fs::create_dir_all(&config.status_dir).map_err(|err| err.to_string())?;
    let protected = gcroot_present(config, context.vmid, context.desired_system);
    let status = deploy_status_value(
        config,
        context,
        DeployStatusOutcome {
            host_has_closure: true,
            container_has_closure: context.current_system == context.desired_system,
            protected,
            last_build_status: "ok",
            last_deploy_status: if last_deploy_status.is_empty() {
                "not-run"
            } else {
                last_deploy_status
            },
            last_error: Value::Null,
        },
    )?;
    write_status_value(&status_path(config, context.vmid), status)
}

fn write_activation_status(
    config: &ReconcileConfig,
    vmid: &str,
    desired_system: &str,
    previous_system: &str,
) -> Result<(), String> {
    let path = status_path(config, vmid);
    let mut status = read_status_object(&path).map_err(|err| {
        format!("status not found for VMID {vmid}; run --build-only first: {err}")
    })?;
    let protected = gcroot_present(config, vmid, desired_system);
    let now = utc_now_seconds_z();
    status.insert("desired_system".to_owned(), json!(desired_system));
    status.insert("current_system".to_owned(), json!(desired_system));
    if !previous_system.is_empty() {
        status.insert("previous_system".to_owned(), json!(previous_system));
    }
    status.insert("container_is_local".to_owned(), json!(true));
    status.insert("host_has_closure".to_owned(), json!(true));
    status.insert("container_has_closure".to_owned(), json!(true));
    status.insert("protected_by_host_gc_root".to_owned(), json!(protected));
    status.insert("host_activated_system".to_owned(), json!(desired_system));
    status.insert("lastBuildStatus".to_owned(), json!("ok"));
    status.insert("lastDeployStatus".to_owned(), json!("ok"));
    status.insert("lastError".to_owned(), Value::Null);
    status.insert("activatedAt".to_owned(), json!(now));
    status.insert("updatedAt".to_owned(), json!(now));
    write_status_object(&path, &status).map_err(|err| err.to_string())
}

fn write_noop_status(
    config: &ReconcileConfig,
    context: &DeployStatusContext<'_>,
) -> Result<(), String> {
    fs::create_dir_all(&config.status_dir).map_err(|err| err.to_string())?;
    let prior = read_optional_status(config, context.vmid);
    let last_build_status = status_string(&prior, "lastBuildStatus");
    let host_has_closure = Path::new(context.desired_system).exists();
    let mut protected = false;
    let was_protected = gcroot_present(config, context.vmid, context.desired_system);
    if host_has_closure {
        let _ = protect_host_closure(config, context.vmid, context.desired_system);
        protected = gcroot_present(config, context.vmid, context.desired_system);
        if !was_protected && protected {
            eprintln!(
                "warning: re-registered missing gcroot for VMID {} ({}); something on this host is reaping proxnix gcroots - check for stray nix-collect-garbage",
                context.vmid, context.desired_system
            );
        }
    }
    let status = deploy_status_value(
        config,
        context,
        DeployStatusOutcome {
            host_has_closure,
            container_has_closure: true,
            protected,
            last_build_status: if last_build_status.is_empty() {
                "not-run"
            } else {
                &last_build_status
            },
            last_deploy_status: "noop-current",
            last_error: Value::Null,
        },
    )?;
    write_status_value(&status_path(config, context.vmid), status)
}

fn write_build_failed_status(
    config: &ReconcileConfig,
    context: &DeployStatusContext<'_>,
    error: &str,
) -> Result<(), String> {
    fs::create_dir_all(&config.status_dir).map_err(|err| err.to_string())?;
    let status = deploy_status_value(
        config,
        context,
        DeployStatusOutcome {
            host_has_closure: false,
            container_has_closure: false,
            protected: false,
            last_build_status: "failed",
            last_deploy_status: "build-failed",
            last_error: json!(error),
        },
    )?;
    write_status_value(&status_path(config, context.vmid), status)
}

fn write_rollback_status(
    config: &ReconcileConfig,
    vmid: &str,
    rollback_system: &str,
) -> Result<(), String> {
    let path = status_path(config, vmid);
    let mut status = read_status_object(&path)
        .map_err(|err| format!("status not found for VMID {vmid}: {err}"))?;
    let now = utc_now_seconds_z();
    status.insert("current_system".to_owned(), json!(rollback_system));
    status.insert("host_activated_system".to_owned(), json!(rollback_system));
    status.insert("container_has_closure".to_owned(), json!(true));
    status.insert("lastDeployStatus".to_owned(), json!("rollback-ok"));
    status.insert("lastError".to_owned(), Value::Null);
    status.insert("rolledBackAt".to_owned(), json!(now));
    status.insert("updatedAt".to_owned(), json!(now));
    write_status_object(&path, &status).map_err(|err| err.to_string())
}

fn update_deploy_status(
    config: &ReconcileConfig,
    vmid: &str,
    deploy_status: &str,
    error: &str,
) -> Result<(), String> {
    let path = status_path(config, vmid);
    let mut status = read_status_object(&path).map_err(|err| {
        format!("status not found for VMID {vmid}; run --build-only first: {err}")
    })?;
    status.insert("lastDeployStatus".to_owned(), json!(deploy_status));
    status.insert(
        "lastError".to_owned(),
        if error.is_empty() {
            Value::Null
        } else {
            json!(error)
        },
    );
    status.insert("updatedAt".to_owned(), json!(utc_now_seconds_z()));
    write_status_object(&path, &status).map_err(|err| err.to_string())
}

fn update_lost_locality_status(
    config: &ReconcileConfig,
    vmid: &str,
    phase: &str,
) -> Result<(), String> {
    let path = status_path(config, vmid);
    let mut status = read_status_object(&path).map_err(|err| {
        format!("status not found for VMID {vmid}; run --build-only first: {err}")
    })?;
    status.insert("local".to_owned(), json!(false));
    status.insert("container_is_local".to_owned(), json!(false));
    status.insert("lastDeployStatus".to_owned(), json!("lost-locality"));
    status.insert(
        "lastError".to_owned(),
        json!(format!("container locality lost before {phase}")),
    );
    status.insert("updatedAt".to_owned(), json!(utc_now_seconds_z()));
    write_status_object(&path, &status).map_err(|err| err.to_string())
}

fn write_status_value(path: &Path, value: Value) -> Result<(), String> {
    let object = value.as_object().cloned().unwrap_or_default();
    write_status_object(path, &object).map_err(|err| err.to_string())
}

fn build_only(config: &ReconcileConfig, selected_vmid: Option<&str>) -> HostResult<()> {
    let vmid = selected_vmid.ok_or("--build-only requires --vmid")?;
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, vmid)?;
    render_reconcile_authority(config)?;
    let container = eval_manifest_container(config, vmid)?;
    if !manifest_container_is_local_or_skip(config, vmid, &container) {
        return Ok(());
    }
    build_selected_without_locks(config, vmid, &container)
}

fn build_selected_without_locks(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
) -> HostResult<()> {
    let hostname = manifest_hostname(container, vmid);
    let source_revision = manifest_source_revision(container);
    let desired_system = manifest_string(container, "system");
    if desired_system.is_empty() {
        return Err(format!("manifest for VMID {vmid} is missing system").into());
    }
    let prior = read_optional_status(config, vmid);
    let current_system = status_current_system(&prior);
    let previous_system = status_previous_system(&prior);
    let last_deploy_status = status_string(&prior, "lastDeployStatus");
    let status_context = DeployStatusContext {
        vmid,
        hostname: &hostname,
        source_revision: &source_revision,
        desired_system: &desired_system,
        current_system: &current_system,
        previous_system: &previous_system,
    };
    if current_system == desired_system {
        write_noop_status(config, &status_context)?;
        println!("{vmid} noop current system matches desired");
        return Ok(());
    }
    let system_path = build_manifest_system(config, vmid, container)?;
    if system_path.is_empty() {
        return Err(format!("nix build produced no output path for VMID {vmid}").into());
    }
    if system_path != desired_system {
        return Err(format!("nix build produced {system_path}, expected {desired_system}").into());
    }
    let built_status_context = DeployStatusContext {
        desired_system: &system_path,
        ..status_context
    };
    write_build_status(
        config,
        &built_status_context,
        if last_deploy_status.is_empty() {
            "not-run"
        } else {
            &last_deploy_status
        },
    )?;
    println!("{vmid} built {system_path}");
    Ok(())
}

fn seed_only(config: &ReconcileConfig, selected_vmid: Option<&str>) -> HostResult<()> {
    let vmid = selected_vmid.ok_or("--seed-only requires --vmid")?;
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, vmid)?;
    if !selected_container_is_local_or_skip(config, vmid) {
        return Ok(());
    }
    let status = read_status_object(&status_path(config, vmid)).map_err(|err| {
        format!("status not found for VMID {vmid}; run --build-only first: {err}")
    })?;
    let desired_system = status_desired_system(&status);
    if desired_system.is_empty() {
        return Err(format!("status for VMID {vmid} does not contain desired_system").into());
    }
    if let Err(err) = seed_closure(config, vmid, &desired_system) {
        update_deploy_status(
            config,
            vmid,
            "failed",
            &format!("closure seed failed: {err}"),
        )?;
        eprintln!("{vmid} seed failed");
        return Err(HostError::silent_exit(2));
    }
    update_deploy_status(config, vmid, "seeded", "")?;
    println!("{vmid} seeded {desired_system}");
    Ok(())
}

fn seed_closure(config: &ReconcileConfig, vmid: &str, system_path: &str) -> Result<(), String> {
    require_nix()?;
    require_nix_store()?;
    with_container_nix_daemon(config, vmid, system_path)
}

fn with_container_nix_daemon(
    config: &ReconcileConfig,
    vmid: &str,
    system_path: &str,
) -> Result<(), String> {
    fs::create_dir_all(&config.run_dir).map_err(|err| err.to_string())?;
    let socket_path = config.run_dir.join(format!("ct-{vmid}.sock"));
    let log_path = config.run_dir.join(format!("ct-{vmid}.socat.log"));
    let _ = fs::remove_file(&socket_path);
    let _ = fs::remove_file(&log_path);
    let mut bridge = start_container_nix_daemon_bridge(config, vmid, &socket_path, &log_path)?;
    let wait_result = wait_for_bridge(config, &socket_path, &mut bridge, &log_path);
    if let Err(err) = wait_result {
        stop_bridge(&mut bridge, &socket_path, &log_path);
        return Err(err);
    }
    let nix_remote = format!("unix://{}", socket_path.display());
    let copy_status = run_container_nix_command(
        config,
        &nix_remote,
        "nix",
        &["copy", "--from", "local", "--no-check-sigs", system_path],
    );
    let realise_status = copy_status.and_then(|_| {
        run_container_nix_command(
            config,
            &nix_remote,
            "nix-store",
            &["--realise", system_path],
        )
    });
    stop_bridge(&mut bridge, &socket_path, &log_path);
    realise_status
}

fn start_container_nix_daemon_bridge(
    config: &ReconcileConfig,
    vmid: &str,
    socket_path: &Path,
    log_path: &Path,
) -> Result<Child, String> {
    let socat = require_socat()?;
    let pct = require_pct()?;
    let connect_cmd = container_nix_daemon_connect_command(config, vmid, &pct);
    let log = File::create(log_path).map_err(|err| err.to_string())?;
    Command::new(socat)
        .arg(format!(
            "UNIX-LISTEN:{},fork,mode=0600",
            socket_path.display()
        ))
        .arg(format!("EXEC:{connect_cmd},nofork"))
        .stdout(Stdio::null())
        .stderr(log)
        .spawn()
        .map_err(|err| format!("failed to start socat: {err}"))
}

fn container_nix_daemon_connect_command(
    config: &ReconcileConfig,
    vmid: &str,
    pct: &Path,
) -> String {
    format!(
        "{} exec {} -- /run/current-system/sw/bin/socat STDIO UNIX-CONNECT:{}",
        shell_quote(&pct.display().to_string()),
        shell_quote(vmid),
        shell_quote(&config.container_nix_daemon_socket)
    )
}

fn wait_for_bridge(
    config: &ReconcileConfig,
    socket_path: &Path,
    bridge: &mut Child,
    log_path: &Path,
) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(config.bridge_ready_timeout);
    loop {
        if socket_path.exists() {
            return Ok(());
        }
        if let Ok(Some(_status)) = bridge.try_wait() {
            return Err(read_socat_log(log_path));
        }
        if Instant::now() > deadline {
            return Err(format!(
                "timed out waiting for Nix daemon bridge socket: {}{}",
                socket_path.display(),
                read_socat_log_suffix(log_path)
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn read_socat_log(log_path: &Path) -> String {
    let content = fs::read_to_string(log_path).unwrap_or_default();
    if content.is_empty() {
        "Nix daemon bridge exited".to_owned()
    } else {
        content
            .lines()
            .map(|line| format!("socat: {line}"))
            .collect::<Vec<_>>()
            .join("\n")
    }
}

fn read_socat_log_suffix(log_path: &Path) -> String {
    let content = read_socat_log(log_path);
    if content == "Nix daemon bridge exited" {
        String::new()
    } else {
        format!("\n{content}")
    }
}

fn stop_bridge(bridge: &mut Child, socket_path: &Path, log_path: &Path) {
    let _ = bridge.kill();
    let _ = bridge.wait();
    let _ = fs::remove_file(socket_path);
    let _ = fs::remove_file(log_path);
}

fn run_container_nix_command(
    config: &ReconcileConfig,
    nix_remote: &str,
    command: &str,
    args: &[&str],
) -> Result<(), String> {
    let mut cmd = if config.nix_command_timeout > 0 {
        let mut timeout = Command::new("timeout");
        timeout
            .arg(config.nix_command_timeout.to_string())
            .arg(command);
        timeout
    } else {
        Command::new(command)
    };
    let output = cmd
        .env("NIX_REMOTE", nix_remote)
        .args(args)
        .output()
        .map_err(|err| format!("failed to run {command}: {err}"))?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        if stderr.is_empty() {
            Err(format!("{command} failed"))
        } else {
            Err(stderr)
        }
    }
}

fn ensure_container_running_for_exec(vmid: &str, start_stopped: bool) -> Result<(), String> {
    let pct = require_pct()?;
    let state = container_state(vmid);
    if state != "running" {
        if !start_stopped {
            return Err(format!(
                "VMID {vmid} is {}; pass --start-stopped to start it for activation",
                if state.is_empty() {
                    "not-running"
                } else {
                    &state
                }
            ));
        }
        let status = Command::new(&pct)
            .arg("start")
            .arg(vmid)
            .status()
            .map_err(|err| format!("failed to start VMID {vmid}: {err}"))?;
        if !status.success() {
            return Err(format!("failed to start VMID {vmid}"));
        }
    }
    for _ in 0..30 {
        if Command::new(&pct)
            .arg("exec")
            .arg(vmid)
            .arg("--")
            .arg("/run/current-system/sw/bin/true")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
        {
            return Ok(());
        }
        thread::sleep(Duration::from_secs(1));
    }
    Err(format!("VMID {vmid} did not become ready for pct exec"))
}

fn restart_container_for_offline_seed(vmid: &str) -> Result<(), String> {
    let state = container_state(vmid);
    if state == "running" {
        let _ = run_pct_command(&["shutdown", vmid, "--timeout", "60"]);
        for _ in 0..60 {
            if container_state(vmid) == "stopped" {
                return start_container_for_offline_seed(vmid);
            }
            thread::sleep(Duration::from_secs(1));
        }
        run_pct_command(&["stop", vmid])?;
    } else if state != "stopped" {
        return Err(format!(
            "VMID {vmid} is {state}; cannot restart for offline seed"
        ));
    }
    start_container_for_offline_seed(vmid)
}

fn start_container_for_offline_seed(vmid: &str) -> Result<(), String> {
    if env_bool("PROXNIX_PCT_START_DEBUG") {
        run_pct_command(&["start", vmid, "--debug"])
    } else {
        run_pct_command(&["start", vmid])
    }
}

fn run_pct_command(args: &[&str]) -> Result<(), String> {
    run_pct_command_with_env(args, &[])
}

fn run_pct_command_with_env(args: &[&str], envs: &[(&str, &str)]) -> Result<(), String> {
    let pct = require_pct()?;
    let mut command = Command::new(pct);
    command.args(args);
    for (key, value) in envs {
        command.env(key, value);
    }
    let output = command
        .output()
        .map_err(|err| format!("failed to run pct {}: {err}", args.join(" ")))?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        Err(format!("pct {} failed", args.join(" ")))
    } else {
        Err(stderr)
    }
}

fn current_system(vmid: &str) -> String {
    let Some(pct) = find_in_path("pct") else {
        return String::new();
    };
    let Ok(output) = Command::new(pct)
        .arg("exec")
        .arg(vmid)
        .arg("--")
        .arg("/run/current-system/sw/bin/readlink")
        .arg("-f")
        .arg("/run/current-system")
        .output()
    else {
        return String::new();
    };
    if !output.status.success() {
        return String::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .replace(['\r', '\n'], "")
        .trim()
        .to_owned()
}

fn activate_system(vmid: &str, system_path: &str) -> Result<(), String> {
    let pct = require_pct()?;
    let output = Command::new(pct)
        .arg("exec")
        .arg(vmid)
        .arg("--")
        .arg(format!("{system_path}/bin/switch-to-configuration"))
        .arg("switch")
        .output()
        .map_err(|err| format!("failed to run activation: {err}"))?;
    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        if stderr.is_empty() {
            Err("activation failed".to_owned())
        } else {
            Err(stderr)
        }
    }
}

fn verify_system(vmid: &str, system_path: &str) -> bool {
    current_system(vmid) == system_path
}

fn activate_only(
    config: &ReconcileConfig,
    selected_vmid: Option<&str>,
    start_stopped: bool,
) -> HostResult<()> {
    let vmid = selected_vmid.ok_or("--activate-only requires --vmid")?;
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, vmid)?;
    if !selected_container_is_local_or_skip(config, vmid) {
        return Ok(());
    }
    let status = read_status_object(&status_path(config, vmid)).map_err(|err| {
        format!("status not found for VMID {vmid}; run --build-only first: {err}")
    })?;
    let desired_system = status_desired_system(&status);
    let mut previous_system = status_previous_system(&status);
    if desired_system.is_empty() {
        return Err(format!("status for VMID {vmid} does not contain desired_system").into());
    }
    ensure_container_running_for_exec(vmid, start_stopped)?;
    let live_current = current_system(vmid);
    if !live_current.is_empty() && live_current != desired_system {
        previous_system = live_current;
    }
    reconcile_activate_phase(config, vmid, &desired_system, &previous_system)?;
    println!("{vmid} activated {desired_system}");
    Ok(())
}

fn reconcile_build_phase(
    config: &ReconcileConfig,
    container: &Value,
    status: &DeployStatusContext<'_>,
) -> HostResult<()> {
    match build_manifest_system(config, status.vmid, container) {
        Ok(built_system) if built_system == status.desired_system => {
            write_build_status(config, status, "not-run").map_err(HostError::from)
        }
        Ok(built_system) if built_system.is_empty() => {
            fail_build(config, status, "nix build produced no output path")
        }
        Ok(built_system) => fail_build(
            config,
            status,
            &format!(
                "nix build produced {built_system}, expected {}",
                status.desired_system
            ),
        ),
        Err(err) => fail_build(config, status, &format!("nix build failed: {err}")),
    }
}

fn fail_build(
    config: &ReconcileConfig,
    status: &DeployStatusContext<'_>,
    reason: &str,
) -> HostResult<()> {
    write_build_failed_status(config, status, reason)?;
    eprintln!("{} build failed", status.vmid);
    Err(HostError::silent_exit(2))
}

fn require_local_container(config: &ReconcileConfig, vmid: &str, phase: &str) -> HostResult<()> {
    if is_local_container(config, vmid) {
        return Ok(());
    }
    update_lost_locality_status(config, vmid, phase)?;
    eprintln!("{vmid} lost locality");
    Err(HostError::silent_exit(2))
}

fn fail_deploy(
    config: &ReconcileConfig,
    vmid: &str,
    reason: &str,
    log_message: &str,
) -> HostResult<()> {
    update_deploy_status(config, vmid, "failed", reason)?;
    eprintln!("{vmid} {log_message}");
    Err(HostError::silent_exit(2))
}

fn reconcile_seed_phase(
    config: &ReconcileConfig,
    vmid: &str,
    desired_system: &str,
) -> HostResult<()> {
    require_local_container(config, vmid, "seed")?;
    if let Err(err) = seed_closure(config, vmid, desired_system) {
        return fail_deploy(
            config,
            vmid,
            &format!("closure seed failed: {err}"),
            "seed failed",
        );
    }
    Ok(update_deploy_status(config, vmid, "seeded", "")?)
}

fn reconcile_offline_seed_phase(
    config: &ReconcileConfig,
    vmid: &str,
    desired_system: &str,
) -> HostResult<()> {
    require_local_container(config, vmid, "offline-seed")?;
    match seed_closure_offline(config, vmid) {
        Ok(()) => Ok(()),
        Err(err) => fail_deploy(
            config,
            vmid,
            &format!("offline closure seed failed for {desired_system}: {err}"),
            "seed failed",
        ),
    }
}

fn reconcile_payload_online_phase(config: &ReconcileConfig, vmid: &str) -> HostResult<()> {
    require_local_container(config, vmid, "payload-online")?;
    let payload = match payload_stage::stage_payload_for_reconcile(vmid) {
        Ok(payload) => payload,
        Err(err) => {
            return fail_deploy(
                config,
                vmid,
                &format!("payload staging failed: {err}"),
                "payload staging failed",
            )
        }
    };
    let sync_result = sync_payload_to_running_ct(vmid, &payload);
    let _ = remove_path_if_exists(&payload.stage_dir);
    if let Err(err) = sync_result {
        return fail_deploy(
            config,
            vmid,
            &format!("online payload sync failed: {err}"),
            "payload sync failed",
        );
    }
    Ok(update_deploy_status(config, vmid, "payload-synced", "")?)
}

fn seed_closure_offline(config: &ReconcileConfig, vmid: &str) -> Result<(), String> {
    let pct = require_pct()?;
    let mounted = MountedCt::mount(&pct, vmid)?;
    let seed_result = seed_offline(
        &SeedOfflineConfig {
            status_dir: config.status_dir.clone(),
        },
        &SeedOfflineOptions {
            vmid: vmid.to_owned(),
            rootfs: mounted.rootfs().to_path_buf(),
        },
    )
    .map_err(|err| err.to_string())
    .and_then(|()| sync_payload_to_mounted_rootfs(vmid, mounted.rootfs()));
    let unmount_result = mounted.unmount();
    if let Err(err) = seed_result {
        let _ = unmount_result;
        return Err(err);
    }
    unmount_result
}

fn start_host_seed(config: &ReconcileConfig, options: &StartHostOptions) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, &options.vmid)?;

    sync_payload_to_mounted_rootfs(&options.vmid, &options.rootfs).map_err(|err| {
        HostError::new(format!(
            "failed to sync start-host payload for VMID {}: {err}",
            options.vmid
        ))
    })?;

    let pve_conf = config.pve_lxc_dir.join(format!("{}.conf", options.vmid));
    if pve_conf_has_tag(&pve_conf, "nix-hold").map_err(|err| {
        HostError::new(format!(
            "failed to read Proxmox tags from {}: {err}",
            pve_conf.display()
        ))
    })? {
        println!(
            "{} start-host skipped closure copy because nix-hold is set",
            options.vmid
        );
        return Ok(());
    }

    let status_file = status_path(config, &options.vmid);
    let status = read_status_object(&status_file).map_err(|err| {
        HostError::new(format!(
            "status for VMID {} is not readable at {}: {err}",
            options.vmid,
            status_file.display()
        ))
    })?;
    let desired_system = status_desired_system(&status);
    if desired_system.is_empty() {
        return Err(HostError::new(format!(
            "status for VMID {} does not contain desired_system; build before start",
            options.vmid
        )));
    }
    let last_build_status = status_string(&status, "lastBuildStatus");
    if last_build_status != "ok" {
        return Err(HostError::new(format!(
            "status for VMID {} has lastBuildStatus={}; build before start",
            options.vmid,
            if last_build_status.is_empty() {
                "unset"
            } else {
                &last_build_status
            }
        )));
    }

    seed_offline(
        &SeedOfflineConfig {
            status_dir: config.status_dir.clone(),
        },
        &SeedOfflineOptions {
            vmid: options.vmid.clone(),
            rootfs: options.rootfs.clone(),
        },
    )
    .map_err(|err| HostError::new(format!("start-host seed failed: {err}")))?;
    Ok(())
}

fn sync_payload_to_mounted_rootfs(vmid: &str, rootfs: &Path) -> Result<(), String> {
    let payload = payload_stage::stage_payload_for_reconcile(vmid)?;
    let result = sync_staged_payload_to_rootfs(rootfs, &payload);
    let _ = remove_path_if_exists(&payload.stage_dir);
    result
}

fn sync_staged_payload_to_rootfs(rootfs: &Path, payload: &StagedPayload) -> Result<(), String> {
    let proxnix_dir = rootfs.join(GUEST_PROXNIX_DIR);
    let runtime_dir = proxnix_dir.join("runtime");
    let build_input_dir = proxnix_dir.join("build-input");
    let runtime_bin_dir = runtime_dir.join("bin");
    let secrets_dir = proxnix_dir.join("secrets");

    fs::create_dir_all(&runtime_dir)
        .map_err(|err| format!("failed to create {}: {err}", runtime_dir.display()))?;

    replace_tree_preserving_metadata(&payload.bind_config_dir, &build_input_dir)?;
    replace_file_preserving_metadata(
        &payload.bind_runtime_dir.join("current-config-hash"),
        &runtime_dir.join("current-config-hash"),
    )?;
    replace_file_preserving_metadata(
        &payload.bind_runtime_dir.join("vmid"),
        &runtime_dir.join("vmid"),
    )?;
    replace_tree_preserving_metadata(&payload.copy_runtime_bin_dir, &runtime_bin_dir)?;
    replace_tree_preserving_metadata(&payload.bind_secrets_dir, &secrets_dir)?;
    Ok(())
}

fn replace_tree_preserving_metadata(source: &Path, dest: &Path) -> Result<(), String> {
    let temp = replacement_path(dest, "new");
    let backup = replacement_path(dest, "old");
    remove_path_if_exists(&temp)
        .map_err(|err| format!("failed to remove {}: {err}", temp.display()))?;
    remove_path_if_exists(&backup)
        .map_err(|err| format!("failed to remove {}: {err}", backup.display()))?;
    copy_tree_preserving_metadata(source, &temp)?;
    replace_path_with_prepared(&temp, dest, &backup)
}

fn replace_file_preserving_metadata(source: &Path, dest: &Path) -> Result<(), String> {
    let temp = replacement_path(dest, "new");
    if temp.exists() || temp.is_symlink() {
        fs::remove_file(&temp)
            .map_err(|err| format!("failed to remove {}: {err}", temp.display()))?;
    }
    copy_file_preserving_metadata(source, &temp)?;
    fs::rename(&temp, dest).map_err(|err| {
        let _ = fs::remove_file(&temp);
        format!(
            "failed to replace {} with {}: {err}",
            dest.display(),
            temp.display()
        )
    })
}

fn replace_path_with_prepared(temp: &Path, dest: &Path, backup: &Path) -> Result<(), String> {
    if dest.exists() || dest.is_symlink() {
        fs::rename(dest, backup).map_err(|err| {
            format!(
                "failed to move {} aside as {}: {err}",
                dest.display(),
                backup.display()
            )
        })?;
    }
    match fs::rename(temp, dest) {
        Ok(()) => {
            let _ = remove_path_if_exists(backup);
            Ok(())
        }
        Err(err) => {
            if backup.exists() || backup.is_symlink() {
                let _ = fs::rename(backup, dest);
            }
            let _ = remove_path_if_exists(temp);
            Err(format!(
                "failed to replace {} with {}: {err}",
                dest.display(),
                temp.display()
            ))
        }
    }
}

fn replacement_path(dest: &Path, label: &str) -> PathBuf {
    let name = dest
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("payload");
    dest.with_file_name(format!(".{name}.proxnix-{label}-{}", std::process::id()))
}

fn copy_tree_preserving_metadata(source: &Path, dest: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(source)
        .map_err(|err| format!("failed to stat {}: {err}", source.display()))?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(format!("expected directory: {}", source.display()));
    }
    fs::create_dir_all(dest)
        .map_err(|err| format!("failed to create {}: {err}", dest.display()))?;
    apply_metadata(dest, &metadata)?;
    for entry in sorted_fs_entries(source)? {
        let entry = entry.map_err(|err| err.to_string())?;
        let source_path = entry.path();
        let dest_path = dest.join(entry.file_name());
        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|err| format!("failed to stat {}: {err}", source_path.display()))?;
        if metadata.is_dir() && !metadata.file_type().is_symlink() {
            copy_tree_preserving_metadata(&source_path, &dest_path)?;
        } else if metadata.is_file() && !metadata.file_type().is_symlink() {
            copy_file_preserving_metadata(&source_path, &dest_path)?;
        } else {
            return Err(format!(
                "unsupported staged payload entry: {}",
                source_path.display()
            ));
        }
    }
    Ok(())
}

fn copy_file_preserving_metadata(source: &Path, dest: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(source)
        .map_err(|err| format!("failed to stat {}: {err}", source.display()))?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(format!("expected file: {}", source.display()));
    }
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
    apply_metadata(dest, &metadata)
}

fn apply_metadata(path: &Path, metadata: &fs::Metadata) -> Result<(), String> {
    chown(path, Some(metadata.uid()), Some(metadata.gid()))
        .map_err(|err| format!("failed to chown {}: {err}", path.display()))?;
    set_mode(path, metadata.permissions().mode() & 0o7777)
        .map_err(|err| format!("failed to chmod {}: {err}", path.display()))
}

fn sorted_fs_entries(path: &Path) -> Result<Vec<io::Result<fs::DirEntry>>, String> {
    let mut entries = fs::read_dir(path)
        .map_err(|err| format!("failed to read {}: {err}", path.display()))?
        .collect::<Vec<_>>();
    entries.sort_by_key(|entry| {
        entry
            .as_ref()
            .map(|entry| entry.file_name())
            .unwrap_or_default()
    });
    Ok(entries)
}

fn sync_payload_to_running_ct(vmid: &str, payload: &StagedPayload) -> Result<(), String> {
    let build_input = guest_proxnix_path("build-input");
    let runtime = guest_proxnix_path("runtime");
    let runtime_bin = guest_proxnix_path("runtime/bin");
    let current_config_hash = guest_proxnix_path("runtime/current-config-hash");
    let runtime_vmid = guest_proxnix_path("runtime/vmid");
    let secrets = guest_proxnix_path("secrets");
    let build_input_tmp = guest_replacement_path("build-input", "new");
    let runtime_bin_tmp = guest_replacement_path("runtime/bin", "new");
    let current_config_hash_tmp = guest_replacement_path("runtime/current-config-hash", "new");
    let runtime_vmid_tmp = guest_replacement_path("runtime/vmid", "new");
    let secrets_tmp = guest_replacement_path("secrets", "new");

    pct_exec(
        vmid,
        &["/run/current-system/sw/bin/rm", "-rf", &build_input_tmp],
    )?;
    pct_exec(
        vmid,
        &["/run/current-system/sw/bin/rm", "-rf", &runtime_bin_tmp],
    )?;
    pct_exec(
        vmid,
        &[
            "/run/current-system/sw/bin/rm",
            "-f",
            &current_config_hash_tmp,
        ],
    )?;
    pct_exec(
        vmid,
        &["/run/current-system/sw/bin/rm", "-f", &runtime_vmid_tmp],
    )?;
    pct_exec(
        vmid,
        &["/run/current-system/sw/bin/rm", "-rf", &secrets_tmp],
    )?;

    push_tree_to_ct(vmid, &payload.bind_config_dir, &build_input_tmp, 0o644)?;
    pct_exec(vmid, &["/run/current-system/sw/bin/mkdir", "-p", &runtime])?;
    push_file_to_ct(
        vmid,
        &payload.bind_runtime_dir.join("current-config-hash"),
        &current_config_hash_tmp,
        0o644,
    )?;
    push_file_to_ct(
        vmid,
        &payload.bind_runtime_dir.join("vmid"),
        &runtime_vmid_tmp,
        0o644,
    )?;
    push_tree_to_ct(vmid, &payload.copy_runtime_bin_dir, &runtime_bin_tmp, 0o555)?;
    push_tree_to_ct(vmid, &payload.bind_secrets_dir, &secrets_tmp, 0o600)?;
    pct_exec(
        vmid,
        &["/run/current-system/sw/bin/chmod", "700", &secrets_tmp],
    )?;

    pct_exec(
        vmid,
        &[
            "/run/current-system/sw/bin/sh",
            "-c",
            &guest_commit_payload_script(
                &[
                    (&build_input, &build_input_tmp),
                    (&runtime_bin, &runtime_bin_tmp),
                    (&secrets, &secrets_tmp),
                ],
                &[
                    (&current_config_hash, &current_config_hash_tmp),
                    (&runtime_vmid, &runtime_vmid_tmp),
                ],
            ),
        ],
    )?;
    Ok(())
}

fn guest_proxnix_path(suffix: &str) -> String {
    format!("/{GUEST_PROXNIX_DIR}/{suffix}")
}

fn guest_replacement_path(dest_suffix: &str, label: &str) -> String {
    let dest = guest_proxnix_path(dest_suffix);
    let (parent, name) = dest.rsplit_once('/').unwrap_or(("", dest.as_str()));
    format!("{parent}/.{name}.proxnix-{label}")
}

fn guest_commit_payload_script(dir_swaps: &[(&str, &str)], file_swaps: &[(&str, &str)]) -> String {
    let mut script = String::from(
        "set -eu\n\
         swap_dir() {\n\
         dest=\"$1\"\n\
         src=\"$2\"\n\
         old=\"${dest}.proxnix-old-$$\"\n\
         rm -rf \"$old\"\n\
         if [ -e \"$dest\" ] || [ -L \"$dest\" ]; then mv \"$dest\" \"$old\"; fi\n\
         if mv \"$src\" \"$dest\"; then rm -rf \"$old\"; else if [ -e \"$old\" ] || [ -L \"$old\" ]; then mv \"$old\" \"$dest\"; fi; exit 1; fi\n\
         }\n",
    );
    for (dest, source) in dir_swaps {
        script.push_str(&format!(
            "swap_dir {} {}\n",
            shell_quote(dest),
            shell_quote(source)
        ));
    }
    for (dest, source) in file_swaps {
        script.push_str(&format!(
            "mv -f {} {}\n",
            shell_quote(source),
            shell_quote(dest)
        ));
    }
    script
}

fn push_tree_to_ct(vmid: &str, source: &Path, dest: &str, file_mode: u32) -> Result<(), String> {
    pct_exec(vmid, &["/run/current-system/sw/bin/mkdir", "-p", dest])?;
    for entry in sorted_fs_entries(source)? {
        let entry = entry.map_err(|err| err.to_string())?;
        let source_path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        let dest_path = format!("{dest}/{name}");
        let metadata = fs::symlink_metadata(&source_path)
            .map_err(|err| format!("failed to stat {}: {err}", source_path.display()))?;
        if metadata.is_dir() && !metadata.file_type().is_symlink() {
            push_tree_to_ct(vmid, &source_path, &dest_path, file_mode)?;
        } else if metadata.is_file() && !metadata.file_type().is_symlink() {
            push_file_to_ct(vmid, &source_path, &dest_path, file_mode)?;
        } else {
            return Err(format!(
                "unsupported staged payload entry: {}",
                source_path.display()
            ));
        }
    }
    Ok(())
}

fn push_file_to_ct(vmid: &str, source: &Path, dest: &str, mode: u32) -> Result<(), String> {
    let pct = require_pct()?;
    let status = Command::new(pct)
        .arg("push")
        .arg(vmid)
        .arg(source)
        .arg(dest)
        .status()
        .map_err(|err| format!("failed to run pct push: {err}"))?;
    if !status.success() {
        return Err(format!("pct push {} {dest} failed", source.display()));
    }
    pct_exec(
        vmid,
        &[
            "/run/current-system/sw/bin/chmod",
            &format!("{mode:o}"),
            dest,
        ],
    )
}

fn pct_exec(vmid: &str, args: &[&str]) -> Result<(), String> {
    let pct = require_pct()?;
    let status = Command::new(pct)
        .arg("exec")
        .arg(vmid)
        .arg("--")
        .args(args)
        .status()
        .map_err(|err| format!("failed to run pct exec: {err}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("pct exec {} failed", args.join(" ")))
    }
}

struct MountedCt {
    pct: PathBuf,
    vmid: String,
    rootfs: PathBuf,
    mounted: bool,
}

impl MountedCt {
    fn mount(pct: &Path, vmid: &str) -> Result<Self, String> {
        if container_state(vmid) != "stopped" {
            return Err(format!("VMID {vmid} must be stopped for offline seed"));
        }
        let output = Command::new(pct)
            .arg("mount")
            .arg(vmid)
            .output()
            .map_err(|err| format!("failed to mount VMID {vmid}: {err}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
            if stderr.is_empty() {
                return Err(format!("failed to mount VMID {vmid}"));
            }
            return Err(stderr);
        }
        let mount_output = String::from_utf8_lossy(&output.stdout);
        let rootfs = parse_pct_mount_rootfs(&mount_output)
            .unwrap_or_else(|| PathBuf::from(format!("/var/lib/lxc/{vmid}/rootfs")));
        validate_mounted_rootfs(&rootfs)?;
        Ok(Self {
            pct: pct.to_path_buf(),
            vmid: vmid.to_owned(),
            rootfs,
            mounted: true,
        })
    }

    fn rootfs(&self) -> &Path {
        &self.rootfs
    }

    fn unmount(mut self) -> Result<(), String> {
        self.unmount_inner()
    }

    fn unmount_inner(&mut self) -> Result<(), String> {
        if !self.mounted {
            return Ok(());
        }
        let output = Command::new(&self.pct)
            .arg("unmount")
            .arg(&self.vmid)
            .output()
            .map_err(|err| format!("failed to unmount VMID {}: {err}", self.vmid))?;
        if output.status.success() {
            self.mounted = false;
            return Ok(());
        }
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        if stderr.is_empty() {
            Err(format!("failed to unmount VMID {}", self.vmid))
        } else {
            Err(stderr)
        }
    }
}

impl Drop for MountedCt {
    fn drop(&mut self) {
        let _ = self.unmount_inner();
    }
}

fn validate_mounted_rootfs(rootfs: &Path) -> Result<(), String> {
    if !rootfs.is_dir() {
        return Err(format!("mounted rootfs not found: {}", rootfs.display()));
    }
    if !rootfs.join("etc").is_dir() {
        return Err(format!(
            "mounted rootfs does not look like a Linux root: {}",
            rootfs.display()
        ));
    }
    if !rootfs.join("nix").is_dir() {
        return Err(format!(
            "mounted rootfs does not contain /nix: {}",
            rootfs.display()
        ));
    }
    Ok(())
}

fn reconcile_activate_phase(
    config: &ReconcileConfig,
    vmid: &str,
    desired_system: &str,
    previous_system: &str,
) -> HostResult<()> {
    require_local_container(config, vmid, "activation")?;
    if let Err(err) = activate_system(vmid, desired_system) {
        return fail_deploy(
            config,
            vmid,
            &format!("activation failed: {err}"),
            "activation failed",
        );
    }
    if !verify_system(vmid, desired_system) {
        return fail_deploy(
            config,
            vmid,
            "activation verification failed",
            "activation verification failed",
        );
    }
    require_local_container(config, vmid, "current status update")?;
    Ok(write_activation_status(
        config,
        vmid,
        desired_system,
        previous_system,
    )?)
}

fn reconcile_selected(
    config: &ReconcileConfig,
    vmid: &str,
    recreate_missing: bool,
    start_stopped: bool,
    online: bool,
    force: bool,
) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, vmid)?;
    render_reconcile_authority(config)?;
    let container = eval_manifest_container(config, vmid)?;
    reconcile_selected_without_locks(
        config,
        vmid,
        &container,
        recreate_missing,
        start_stopped,
        online,
        force,
    )
}

fn reconcile_selected_without_locks(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
    recreate_missing: bool,
    start_stopped: bool,
    online: bool,
    force: bool,
) -> HostResult<()> {
    if !ensure_container_exists(config, vmid, container, recreate_missing)? {
        return Ok(());
    }
    reject_held_container_unless_forced(config, vmid, force)?;
    if online {
        return reconcile_selected_online_without_locks(config, vmid, container, start_stopped);
    }
    reconcile_selected_offline_without_locks(config, vmid, container, start_stopped)
}

fn reconcile_selected_online_without_locks(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
    start_stopped: bool,
) -> HostResult<()> {
    let hostname = manifest_hostname(container, vmid);
    let source_revision = manifest_source_revision(container);
    let desired_system = manifest_string(container, "system");
    if desired_system.is_empty() {
        return Err(format!("manifest for VMID {vmid} is missing system").into());
    }
    ensure_container_running_for_exec(vmid, start_stopped)?;
    let live_current = current_system(vmid);
    let prior = read_optional_status(config, vmid);
    let prior_previous = status_previous_system(&prior);
    if live_current == desired_system {
        reconcile_payload_online_phase(config, vmid)?;
        let status_context = DeployStatusContext {
            vmid,
            hostname: &hostname,
            source_revision: &source_revision,
            desired_system: &desired_system,
            current_system: &desired_system,
            previous_system: &prior_previous,
        };
        write_noop_status(config, &status_context)?;
        println!("{vmid} noop current system matches desired");
        return Ok(());
    }
    let previous_system = if !live_current.is_empty() && live_current != desired_system {
        live_current.clone()
    } else {
        prior_previous
    };
    let status_context = DeployStatusContext {
        vmid,
        hostname: &hostname,
        source_revision: &source_revision,
        desired_system: &desired_system,
        current_system: &live_current,
        previous_system: &previous_system,
    };
    reconcile_build_phase(config, container, &status_context)?;
    reconcile_seed_phase(config, vmid, &desired_system)?;
    reconcile_payload_online_phase(config, vmid)?;
    reconcile_activate_phase(config, vmid, &desired_system, &previous_system)?;
    println!("{vmid} activated {desired_system}");
    Ok(())
}

fn reconcile_selected_offline_without_locks(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
    start_stopped: bool,
) -> HostResult<()> {
    let hostname = manifest_hostname(container, vmid);
    let source_revision = manifest_source_revision(container);
    let desired_system = manifest_string(container, "system");
    if desired_system.is_empty() {
        return Err(format!("manifest for VMID {vmid} is missing system").into());
    }

    let initial_state = container_state(vmid);
    let was_running = initial_state == "running";
    if initial_state.is_empty() {
        return Err(format!("VMID {vmid} state is unknown").into());
    }
    let live_current = if was_running {
        current_system(vmid)
    } else {
        String::new()
    };
    let prior = read_optional_status(config, vmid);
    let prior_previous = status_previous_system(&prior);
    let prior_current = status_current_system(&prior);
    let current_system_for_status = if live_current.is_empty() {
        prior_current
    } else {
        live_current.clone()
    };
    if was_running && live_current == desired_system {
        reconcile_payload_online_phase(config, vmid)?;
        let status_context = DeployStatusContext {
            vmid,
            hostname: &hostname,
            source_revision: &source_revision,
            desired_system: &desired_system,
            current_system: &desired_system,
            previous_system: &prior_previous,
        };
        write_noop_status(config, &status_context)?;
        println!("{vmid} noop current system matches desired");
        return Ok(());
    }
    let previous_system =
        if !current_system_for_status.is_empty() && current_system_for_status != desired_system {
            current_system_for_status.clone()
        } else {
            prior_previous
        };
    let status_context = DeployStatusContext {
        vmid,
        hostname: &hostname,
        source_revision: &source_revision,
        desired_system: &desired_system,
        current_system: &current_system_for_status,
        previous_system: &previous_system,
    };
    reconcile_build_phase(config, container, &status_context)?;
    reconcile_offline_seed_phase(config, vmid, &desired_system)?;

    if was_running || start_stopped {
        boot_seeded_container(config, vmid, &desired_system, &previous_system, was_running)?;
    } else {
        println!("{vmid} offline-seeded {desired_system}");
    }
    Ok(())
}

fn boot_seeded_container(
    config: &ReconcileConfig,
    vmid: &str,
    desired_system: &str,
    previous_system: &str,
    restart: bool,
) -> HostResult<()> {
    if restart {
        restart_container_for_offline_seed(vmid)?;
    } else {
        start_container_for_offline_seed(vmid)?;
    }
    ensure_container_running_for_exec(vmid, false)?;
    if !verify_system(vmid, desired_system) {
        return fail_deploy(
            config,
            vmid,
            "offline activation verification failed",
            "activation verification failed",
        );
    }
    write_activation_status(config, vmid, desired_system, previous_system)?;
    println!("{vmid} activated {desired_system}");
    Ok(())
}

fn reconcile_all_running(
    config: &ReconcileConfig,
    recreate_missing: bool,
    start_stopped: bool,
    online: bool,
    force: bool,
) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    render_reconcile_authority(config)?;
    let manifest = eval_node_manifest(config)?;
    let vmids = sorted_vmids(&manifest);
    if vmids.is_empty() {
        println!(
            "no managed containers in proxnix.nodes.{}",
            config.node_name
        );
        return Ok(());
    }
    for vmid in vmids {
        let container = manifest_container(&manifest, &vmid)?;
        if !manifest_container_is_local_or_skip(config, &vmid, container) {
            continue;
        }
        let state = container_state(&vmid);
        if state != "running" && !(recreate_missing && state.is_empty()) {
            println!(
                "{vmid} skip {}",
                if state.is_empty() {
                    "not-running"
                } else {
                    &state
                }
            );
            continue;
        }
        let _container = take_container_lock(config, &vmid)?;
        reconcile_selected_without_locks(
            config,
            &vmid,
            container,
            recreate_missing,
            start_stopped,
            online,
            force,
        )?;
    }
    Ok(())
}

fn reconcile_auto_tag(config: &ReconcileConfig, force: bool) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    render_reconcile_authority(config)?;
    let manifest = eval_node_manifest(config)?;
    let vmids = sorted_vmids(&manifest);
    if vmids.is_empty() {
        println!(
            "no managed containers in proxnix.nodes.{}",
            config.node_name
        );
        return Ok(());
    }
    for vmid in vmids {
        let container = manifest_container(&manifest, &vmid)?;
        if !manifest_container_is_local_or_skip(config, &vmid, container) {
            continue;
        }
        let _container = take_container_lock(config, &vmid)?;
        let pve_conf = config.pve_lxc_dir.join(format!("{vmid}.conf"));
        let nix_hold = pve_conf_has_tag(&pve_conf, "nix-hold").unwrap_or(false);
        let nix_stage = pve_conf_has_tag(&pve_conf, "nix-stage").unwrap_or(false);
        let nix_auto = pve_conf_has_tag(&pve_conf, "nix-auto").unwrap_or(false);

        if nix_hold && !force {
            build_selected_without_locks(config, &vmid, container)?;
            println!("{vmid} hold build-only");
            continue;
        }
        if !nix_auto && !nix_stage {
            build_selected_without_locks(config, &vmid, container)?;
            println!("{vmid} build-only no-runtime-tag");
            continue;
        }

        let state = container_state(&vmid);
        match (nix_auto, nix_stage, state.as_str()) {
            (true, _, "running") => reconcile_selected_without_locks(
                config, &vmid, container, false, false, true, force,
            )?,
            (true, _, "stopped") => reconcile_selected_without_locks(
                config, &vmid, container, false, false, false, force,
            )?,
            (false, true, "stopped") => reconcile_selected_without_locks(
                config, &vmid, container, false, false, false, force,
            )?,
            (false, true, "running") => {
                build_selected_without_locks(config, &vmid, container)?;
                println!("{vmid} stage build-only running");
            }
            (_, _, "") => {
                println!("{vmid} skip state-unknown");
                continue;
            }
            (_, _, other) => {
                println!("{vmid} skip {other}");
                continue;
            }
        }
    }
    Ok(())
}

fn rootfs_storage(rootfs: &str) -> String {
    rootfs
        .split_once(':')
        .map(|(storage, _)| storage.to_owned())
        .unwrap_or_default()
}

fn rootfs_disk_gb(rootfs: &str) -> String {
    rootfs
        .split(',')
        .find_map(|part| part.strip_prefix("size="))
        .map(|size| size.trim_end_matches(['G', 'g']).to_owned())
        .unwrap_or_default()
}

fn net0_part(net0: &str, key: &str) -> String {
    net0.split(',')
        .find_map(|part| {
            let (part_key, value) = part.split_once('=')?;
            (part_key == key).then(|| value.to_owned())
        })
        .unwrap_or_default()
}

fn ensure_container_exists(
    config: &ReconcileConfig,
    vmid: &str,
    container: &Value,
    recreate_missing: bool,
) -> Result<bool, String> {
    if is_local_container(config, vmid) {
        return Ok(true);
    }
    if !manifest_declares_local(&config.node_name, container) {
        println!("{vmid} skip not-local");
        return Ok(false);
    }
    if !recreate_missing {
        println!("{vmid} skip missing-local");
        return Ok(false);
    }
    let placement_node = placement_field(container, "node");
    let target_node = placement_field(container, "targetNode");
    if placement_node != config.node_name
        && target_node != config.node_name
        && !placement_bool(container, "local")
    {
        return Err(format!(
            "VMID {vmid} is not local to {}; --recreate-missing requires placement.local=true or placement.node/placement.targetNode to match this node",
            config.node_name
        ));
    }
    let hostname = manifest_hostname(container, vmid);
    let rootfs = pve_field(container, "rootfs");
    let net0 = pve_field(container, "net0");
    let mut args = vec![
        "--vmid".to_owned(),
        vmid.to_owned(),
        "--hostname".to_owned(),
        hostname,
        "--yes".to_owned(),
        "--no-start".to_owned(),
        "--no-doctor".to_owned(),
    ];
    push_arg_if_present(&mut args, "--template", &pve_field(container, "template"));
    push_arg_if_present(&mut args, "--storage", &rootfs_storage(&rootfs));
    push_arg_if_present(&mut args, "--disk", &rootfs_disk_gb(&rootfs));
    push_arg_if_present(&mut args, "--memory", &pve_field(container, "memory"));
    push_arg_if_present(&mut args, "--swap", &pve_field(container, "swap"));
    push_arg_if_present(&mut args, "--cores", &pve_field(container, "cores"));
    push_arg_if_present(&mut args, "--bridge", &net0_part(&net0, "bridge"));
    push_arg_if_present(&mut args, "--ip", &net0_part(&net0, "ip"));
    push_arg_if_present(&mut args, "--gw", &net0_part(&net0, "gw"));
    push_arg_if_present(
        &mut args,
        "--unprivileged",
        &pve_field(container, "unprivileged"),
    );
    run_create_lxc(config, &args)?;
    if !is_local_container(config, vmid) {
        return Err(format!(
            "proxnix-host create-lxc completed but VMID {vmid} is still not local"
        ));
    }
    Ok(true)
}

fn push_arg_if_present(args: &mut Vec<String>, flag: &str, value: &str) {
    if !value.is_empty() {
        args.push(flag.to_owned());
        args.push(value.to_owned());
    }
}

fn run_create_lxc(config: &ReconcileConfig, args: &[String]) -> Result<(), String> {
    if let Some(create_lxc) = &config.create_lxc {
        let status = Command::new(create_lxc)
            .args(args)
            .status()
            .map_err(|err| format!("failed to run PROXNIX_CREATE_LXC: {err}"))?;
        if status.success() {
            return Ok(());
        }
        return Err("PROXNIX_CREATE_LXC failed".to_owned());
    }
    create_lxc::main(args).map_err(|err| format!("create-lxc failed: {err}"))
}

fn rollback_container(config: &ReconcileConfig, vmid: &str, start_stopped: bool) -> HostResult<()> {
    let _global = take_global_lock(config)?;
    let _container = take_container_lock(config, vmid)?;
    if !selected_container_is_local_or_skip(config, vmid) {
        return Ok(());
    }
    let status = read_status_object(&status_path(config, vmid))
        .map_err(|err| format!("status not found for VMID {vmid}: {err}"))?;
    let previous_system = status_previous_system(&status);
    if previous_system.is_empty() {
        return Err(format!("status for VMID {vmid} does not contain previous_system").into());
    }
    ensure_container_running_for_exec(vmid, start_stopped)?;
    if let Err(err) = seed_closure(config, vmid, &previous_system) {
        update_deploy_status(
            config,
            vmid,
            "rollback-failed",
            &format!("rollback seed failed: {err}"),
        )?;
        eprintln!("{vmid} rollback seed failed");
        return Err(HostError::silent_exit(2));
    }
    if let Err(err) = activate_system(vmid, &previous_system) {
        update_deploy_status(
            config,
            vmid,
            "rollback-failed",
            &format!("rollback activation failed: {err}"),
        )?;
        eprintln!("{vmid} rollback activation failed");
        return Err(HostError::silent_exit(2));
    }
    if !verify_system(vmid, &previous_system) {
        update_deploy_status(
            config,
            vmid,
            "rollback-failed",
            "rollback verification failed",
        )?;
        eprintln!("{vmid} rollback verification failed");
        return Err(HostError::silent_exit(2));
    }
    write_rollback_status(config, vmid, &previous_system)?;
    println!("{vmid} rolled back {previous_system}");
    Ok(())
}

impl BuildGoldenConfig {
    fn from_env() -> Self {
        let ProxnixPaths {
            root,
            authority,
            pve_lxc_dir,
            gcroot_dir,
            authority_render,
        } = proxnix_paths_from_env();
        Self {
            root,
            authority,
            pve_lxc_dir,
            gcroot_dir,
            authority_render,
        }
    }
}

fn parse_build_golden_args(args: &[String]) -> Result<BuildGoldenOptions, String> {
    let mut node_name = env::var("PROXNIX_NODE_NAME").unwrap_or_else(|_| default_node_name());
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--node-name" => node_name = take_arg(args, &mut index, "--node-name")?,
            "-h" | "--help" => {
                build_golden_usage();
                return Ok(BuildGoldenOptions { node_name });
            }
            other => return Err(format!("unknown build-golden argument: {other}")),
        }
        index += 1;
    }
    Ok(BuildGoldenOptions { node_name })
}

fn build_golden_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host reconcile build-golden [--node-name <name>]

Renders the host authority and builds
nixosConfigurations.proxnix-golden-template.config.system.build.toplevel.
"
    );
}

fn parse_seed_offline_args(args: &[String]) -> Result<SeedOfflineOptions, String> {
    let mut vmid = None;
    let mut rootfs = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--rootfs" => rootfs = Some(PathBuf::from(take_arg(args, &mut index, "--rootfs")?)),
            "-h" | "--help" => {
                seed_offline_usage();
                return Ok(SeedOfflineOptions {
                    vmid: String::new(),
                    rootfs: PathBuf::new(),
                });
            }
            other => return Err(format!("unknown seed-offline argument: {other}")),
        }
        index += 1;
    }
    let vmid = vmid.ok_or("--vmid is required")?;
    if !valid_vmid(&vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    let rootfs = rootfs.ok_or("--rootfs is required")?;
    if !rootfs.is_dir() {
        return Err(format!("rootfs not found: {}", rootfs.display()));
    }
    if !rootfs.join("etc").is_dir() {
        return Err(format!(
            "rootfs does not look like a Linux root: {}",
            rootfs.display()
        ));
    }
    Ok(SeedOfflineOptions { vmid, rootfs })
}

fn parse_seed_args(args: &[String]) -> Result<SeedOptions, String> {
    let mut vmid = None;
    let mut rootfs = None;
    let mut passthrough_args = Vec::new();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => {
                let value = take_arg(args, &mut index, "--vmid")?;
                vmid = Some(value.clone());
                passthrough_args.push("--vmid".to_owned());
                passthrough_args.push(value);
            }
            "--rootfs" => {
                rootfs = Some(PathBuf::from(take_arg(args, &mut index, "--rootfs")?));
            }
            "-h" | "--help" => {
                return Ok(SeedOptions {
                    vmid: String::new(),
                    rootfs: None,
                    passthrough_args,
                    help: true,
                });
            }
            other => passthrough_args.push(other.to_owned()),
        }
        index += 1;
    }
    let vmid = vmid.ok_or("--vmid is required")?;
    if !valid_vmid(&vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    Ok(SeedOptions {
        vmid,
        rootfs,
        passthrough_args,
        help: false,
    })
}

fn parse_start_host_args(args: &[String]) -> Result<StartHostOptions, String> {
    let mut vmid = None;
    let mut rootfs = None;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--rootfs" => rootfs = Some(PathBuf::from(take_arg(args, &mut index, "--rootfs")?)),
            "-h" | "--help" => {
                return Ok(StartHostOptions {
                    vmid: String::new(),
                    rootfs: PathBuf::new(),
                    help: true,
                });
            }
            "lxc" | "start-host" => {}
            other if vmid.is_none() && valid_vmid(other) => vmid = Some(other.to_owned()),
            other => return Err(format!("unknown start-host argument: {other}")),
        }
        index += 1;
    }
    let vmid = vmid
        .or_else(|| env::var("LXC_NAME").ok())
        .ok_or("VMID not set (pass --vmid or export LXC_NAME)")?;
    if !valid_vmid(&vmid) {
        return Err(format!("invalid VMID: {vmid}"));
    }
    let rootfs = rootfs
        .or_else(|| env::var_os("LXC_ROOTFS_MOUNT").map(PathBuf::from))
        .or_else(|| {
            env::var_os("LXC_PID").map(|pid| {
                let pid = pid.to_string_lossy();
                PathBuf::from(format!("/proc/{pid}/root"))
            })
        })
        .ok_or("rootfs not set (pass --rootfs, export LXC_ROOTFS_MOUNT, or export LXC_PID)")?;
    validate_mounted_rootfs(&rootfs)?;
    Ok(StartHostOptions {
        vmid,
        rootfs,
        help: false,
    })
}

fn seed_action(args: &[String]) -> Result<SeedAction, String> {
    let options = parse_seed_args(args)?;
    if options.help {
        return Ok(SeedAction::Help);
    }
    if let Some(rootfs) = options.rootfs {
        if !rootfs.is_dir() {
            return Err(format!("rootfs not found: {}", rootfs.display()));
        }
        if !rootfs.join("etc").is_dir() {
            return Err(format!(
                "rootfs does not look like a Linux root: {}",
                rootfs.display()
            ));
        }
        return Ok(SeedAction::Offline(SeedOfflineOptions {
            vmid: options.vmid,
            rootfs,
        }));
    }
    reject_stopped_seed_target(&options.vmid)?;
    Ok(SeedAction::Running(options.passthrough_args))
}

fn reject_stopped_seed_target(vmid: &str) -> Result<(), String> {
    if pct_status_is_stopped(vmid) {
        return Err(format!(
            "VMID {vmid} is stopped; pass --rootfs <mounted-rootfs> or run proxnix-host reconcile --vmid {vmid}"
        ));
    }
    Ok(())
}

fn pct_status_is_stopped(vmid: &str) -> bool {
    let Some(pct) = find_in_path("pct") else {
        return false;
    };
    let Ok(output) = Command::new(pct).arg("status").arg(vmid).output() else {
        return false;
    };
    if !output.status.success() {
        return false;
    }
    let status = String::from_utf8_lossy(&output.stdout);
    let mut words = status.split_whitespace();
    let _label = words.next();
    words.next() == Some("stopped")
}

fn start_host_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host start-host [--vmid <id>] [--rootfs </path/to/mounted/rootfs>]

LXC start-host hook entrypoint. It never builds. It refreshes cheap payload
files and idempotently copies the already-built desired closure into the
mounted rootfs before container init starts.
"
    );
}

fn seed_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host reconcile seed --vmid <id> [--rootfs </path/to/mounted/rootfs>]

Seeds into a running CT with pct exec through the main reconciler. When
--rootfs is provided, seeds the stopped container rootfs directly.
"
    );
}

fn seed_offline_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host reconcile seed-offline --vmid <id> --rootfs </path/to/mounted/rootfs>

Copies the host-built desired NixOS closure into a stopped container rootfs and
sets the rootfs NixOS system profile so the container boots the desired system.
"
    );
}

fn build_golden(config: &BuildGoldenConfig, options: &BuildGoldenOptions) -> io::Result<()> {
    render_build_authority(config, &options.node_name)?;
    fs::create_dir_all(&config.gcroot_dir)?;
    let root_path = config.gcroot_dir.join("golden-template");
    let system_path = nix_build_golden(&config.authority, &root_path)?;
    if system_path.is_empty() {
        return Err(io::Error::other("nix build produced no output path"));
    }
    println!("golden-template built {system_path}");
    Ok(())
}

fn render_build_authority(config: &BuildGoldenConfig, node_name: &str) -> io::Result<()> {
    if let Some(authority_render) = &config.authority_render {
        let status = Command::new(authority_render)
            .arg("--root")
            .arg(&config.root)
            .arg("--authority")
            .arg(&config.authority)
            .arg("--pve-lxc-dir")
            .arg(&config.pve_lxc_dir)
            .arg("--node-name")
            .arg(node_name)
            .stdout(Stdio::null())
            .status()?;
        if status.success() {
            return Ok(());
        }
        return Err(io::Error::other("PROXNIX_AUTHORITY_RENDER failed"));
    }

    render_authority(
        &config.root,
        &config.authority,
        &config.pve_lxc_dir,
        node_name,
    )?;
    Ok(())
}

fn nix_build_golden(authority: &Path, root_path: &Path) -> io::Result<String> {
    let nix = find_in_path("nix")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "nix not found"))?;
    let output = Command::new(nix)
        .arg("build")
        .arg("--out-link")
        .arg(root_path)
        .arg("--print-out-paths")
        .arg(format!(
            "{}#nixosConfigurations.proxnix-golden-template.config.system.build.toplevel",
            authority.display()
        ))
        .output()?;
    if !output.status.success() {
        return Err(io::Error::other("nix build failed"));
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .replace(['\r', '\n'], "")
        .trim()
        .to_owned())
}

fn seed_offline(config: &SeedOfflineConfig, options: &SeedOfflineOptions) -> io::Result<()> {
    let status_file = config.status_dir.join(format!("{}.json", options.vmid));
    if !status_file.is_file() {
        println!("{} offline seed skipped no status", options.vmid);
        return Ok(());
    }

    let mut status = read_status_object(&status_file)?;
    record_guest_activation_marker(&mut status, &options.rootfs);
    write_status_object(&status_file, &status)?;

    let desired_system = status_desired_system(&status);
    let current_system = status_current_system(&status);
    let previous_system = status_previous_system(&status);
    let last_build_status = status_string(&status, "lastBuildStatus");

    if desired_system.is_empty() {
        println!("{} offline seed skipped no desired system", options.vmid);
        return Ok(());
    }
    if last_build_status != "ok" {
        println!("{} offline seed skipped build not ok", options.vmid);
        return Ok(());
    }

    nix_copy_to_rootfs(&options.rootfs, &desired_system)?;
    let switch = rootfs_path(
        &options.rootfs,
        &format!("{desired_system}/bin/switch-to-configuration"),
    );
    if !is_executable(&switch) {
        return Err(io::Error::other(format!(
            "seeded system is missing switch-to-configuration: {desired_system}"
        )));
    }
    set_rootfs_system_profile(&options.rootfs, &desired_system)?;

    let runtime_dir = options.rootfs.join(GUEST_PROXNIX_DIR).join("runtime");
    fs::create_dir_all(&runtime_dir)?;
    write_mode(
        &runtime_dir.join("next-system"),
        &format!("{desired_system}\n"),
        0o644,
    )?;
    if !previous_system.is_empty() {
        write_mode(
            &runtime_dir.join("previous-system"),
            &format!("{previous_system}\n"),
            0o644,
        )?;
    } else if !current_system.is_empty() {
        write_mode(
            &runtime_dir.join("previous-system"),
            &format!("{current_system}\n"),
            0o644,
        )?;
    } else {
        remove_file_if_exists(&runtime_dir.join("previous-system"))?;
    }

    mark_offline_seeded(&mut status, &desired_system);
    write_status_object(&status_file, &status)?;
    println!("{} offline-seeded {desired_system}", options.vmid);
    Ok(())
}

fn read_status_object(path: &Path) -> io::Result<Map<String, Value>> {
    fs::read_to_string(path)
        .and_then(|content| {
            serde_json::from_str::<Value>(&content)
                .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))
        })
        .map(|value| value.as_object().cloned().unwrap_or_default())
}

fn write_status_object(path: &Path, status: &Map<String, Value>) -> io::Result<()> {
    let rendered = serde_json::to_string_pretty(&Value::Object(status.clone()))
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?
        + "\n";
    let tmp = path.with_file_name(format!(
        "{}.tmp",
        path.file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("status.json")
    ));
    fs::write(&tmp, rendered)?;
    fs::rename(tmp, path)
}

fn status_string(status: &Map<String, Value>, key: &str) -> String {
    status
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn status_desired_system(status: &Map<String, Value>) -> String {
    status_string(status, "desired_system")
}

fn status_current_system(status: &Map<String, Value>) -> String {
    status_string(status, "current_system")
}

fn status_previous_system(status: &Map<String, Value>) -> String {
    status_string(status, "previous_system")
}

fn record_guest_activation_marker(status: &mut Map<String, Value>, rootfs: &Path) {
    let marker = rootfs
        .join(GUEST_PROXNIX_DIR)
        .join("runtime/activated-system");
    let activated = fs::read_to_string(marker)
        .ok()
        .map(|value| value.replace(['\r', '\n'], ""))
        .unwrap_or_default();
    if activated.is_empty() {
        return;
    }
    let desired = status_desired_system(status);
    let current = status_current_system(status);
    let drift = (!desired.is_empty() && activated != desired)
        || (!current.is_empty() && current != activated);
    status.insert("guest_activated_system".to_owned(), json!(activated));
    status.insert("guestActivationMarkerDrift".to_owned(), json!(drift));
    status.insert("updatedAt".to_owned(), json!(utc_now_seconds_z()));
}

fn mark_offline_seeded(status: &mut Map<String, Value>, desired_system: &str) {
    status.insert("desired_system".to_owned(), json!(desired_system));
    status.insert("container_has_closure".to_owned(), json!(true));
    status.insert("lastDeployStatus".to_owned(), json!("offline-seeded"));
    status.insert("lastError".to_owned(), Value::Null);
    status.insert("updatedAt".to_owned(), json!(utc_now_seconds_z()));
}

fn profile_target_store_path(profiles_dir: &Path, link_path: &Path) -> Option<String> {
    if !fs::symlink_metadata(link_path)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        return None;
    }
    let target = fs::read_link(link_path).ok()?;
    if target.starts_with("/nix/store") {
        return Some(target.display().to_string());
    }
    let target = if target.starts_with("/nix/var/nix/profiles") {
        profiles_dir.join(target.file_name()?)
    } else if target.is_absolute() {
        return None;
    } else {
        profiles_dir.join(target)
    };
    if !fs::symlink_metadata(&target)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        return None;
    }
    let generation_target = fs::read_link(target).ok()?;
    generation_target
        .starts_with("/nix/store")
        .then(|| generation_target.display().to_string())
}

fn next_system_generation_number(profiles_dir: &Path) -> io::Result<u64> {
    let mut max = 0;
    if !profiles_dir.is_dir() {
        return Ok(1);
    }
    for entry in fs::read_dir(profiles_dir)? {
        let name = entry?.file_name().to_string_lossy().into_owned();
        let Some(number) = name
            .strip_prefix("system-")
            .and_then(|value| value.strip_suffix("-link"))
            .and_then(|value| value.parse::<u64>().ok())
        else {
            continue;
        };
        max = max.max(number);
    }
    Ok(max + 1)
}

fn rootfs_sbin_dir(rootfs: &Path) -> PathBuf {
    let sbin_path = rootfs.join("sbin");
    if fs::symlink_metadata(&sbin_path)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        if let Ok(target) = fs::read_link(&sbin_path) {
            if target.is_absolute() {
                return rootfs.join(target.strip_prefix("/").unwrap_or(&target));
            }
            return rootfs.join(target);
        }
    }
    sbin_path
}

fn set_rootfs_system_profile(rootfs: &Path, desired_system: &str) -> io::Result<()> {
    if !desired_system.starts_with("/nix/store/") {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("desired system is not a store path: {desired_system}"),
        ));
    }
    if !is_executable(&rootfs_path(rootfs, &format!("{desired_system}/init"))) {
        return Err(io::Error::other(format!(
            "seeded system is missing init: {desired_system}"
        )));
    }

    let profiles_dir = rootfs.join("nix/var/nix/profiles");
    let gcroots_dir = rootfs.join("nix/var/nix/gcroots");
    let sbin_dir = rootfs_sbin_dir(rootfs);
    fs::create_dir_all(&profiles_dir)?;
    fs::create_dir_all(&gcroots_dir)?;
    fs::create_dir_all(&sbin_dir)?;

    let current_profile = profile_target_store_path(&profiles_dir, &profiles_dir.join("system"));
    if current_profile.as_deref() != Some(desired_system) {
        let mut generation = next_system_generation_number(&profiles_dir)?;
        loop {
            let generation_link = profiles_dir.join(format!("system-{generation}-link"));
            if !generation_link.exists() {
                symlink(desired_system, &generation_link)?;
                replace_symlink(
                    &profiles_dir.join("system"),
                    Path::new(&format!("system-{generation}-link")),
                )?;
                break;
            }
            generation += 1;
        }
    }

    let profiles_gcroot = gcroots_dir.join("profiles");
    if !profiles_gcroot.exists()
        && !fs::symlink_metadata(&profiles_gcroot)
            .ok()
            .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        symlink("/nix/var/nix/profiles", profiles_gcroot)?;
    }

    let init_path = sbin_dir.join("init");
    if init_path.exists()
        && !fs::symlink_metadata(&init_path)
            .ok()
            .is_some_and(|metadata| metadata.file_type().is_symlink())
    {
        if !init_path.is_file() {
            return Err(io::Error::other(format!(
                "refusing to replace non-file {}",
                init_path.display()
            )));
        }
        fs::remove_file(&init_path)?;
    }
    replace_symlink(&init_path, Path::new("/nix/var/nix/profiles/system/init"))
}

fn replace_symlink(link: &Path, target: &Path) -> io::Result<()> {
    remove_path_if_exists(link)?;
    symlink(target, link)
}

fn nix_copy_to_rootfs(rootfs: &Path, desired_system: &str) -> io::Result<()> {
    let nix = find_in_path("nix")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "nix not found"))?;
    let status = Command::new(nix)
        .arg("copy")
        .arg("--no-check-sigs")
        .arg("--to")
        .arg(format!("local?root={}", rootfs.display()))
        .arg(desired_system)
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other("nix copy failed"))
    }
}

fn is_executable(path: &Path) -> bool {
    fs::metadata(path)
        .map(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

fn write_mode(path: &Path, content: &str, mode: u32) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, content)?;
    set_mode(path, mode)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{TestTemp, ENV_LOCK};
    use std::os::unix::fs::PermissionsExt;

    fn write_executable(path: &Path, content: &str) {
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    fn fake_authority_render() -> &'static str {
        "#!/bin/sh\nset -eu\nroot=\"\"\nauthority=\"\"\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    --root) root=\"$2\"; shift 2;;\n    --authority) authority=\"$2\"; shift 2;;\n    *) shift;;\n  esac\ndone\n[ -n \"$authority\" ] || exit 3\nmkdir -p \"$authority\"\nif [ -n \"$root\" ] && [ -f \"$root/flake.lock\" ]; then cp \"$root/flake.lock\" \"$authority/flake.lock\"; fi\n"
    }

    #[test]
    fn pve_field_normalizes_manifest_bools_for_pct_cli() {
        let container = json!({
            "pve": {
                "unprivileged": true,
                "onboot": false,
                "memory": 2048,
                "rootfs": "local-lvm:vm-101-disk-0,size=8G"
            }
        });

        assert_eq!(pve_field(&container, "unprivileged"), "1");
        assert_eq!(pve_field(&container, "onboot"), "0");
        assert_eq!(pve_field(&container, "memory"), "2048");
        assert_eq!(
            pve_field(&container, "rootfs"),
            "local-lvm:vm-101-disk-0,size=8G"
        );
    }

    #[test]
    fn manifest_placement_can_declare_planned_local_container() {
        let container = json!({
            "placement": {
                "local": true,
                "node": null,
                "targetNode": "pve1",
                "observedPveConfig": false,
            }
        });

        assert!(manifest_declares_local("pve1", &container));
    }

    #[test]
    fn manifest_placement_rejects_other_target_node() {
        let container = json!({
            "placement": {
                "local": false,
                "node": null,
                "targetNode": "pve2",
                "observedPveConfig": false,
            }
        });

        assert!(!manifest_declares_local("pve1", &container));
    }

    #[test]
    fn replace_tree_removes_stale_destination_entries_after_copy_succeeds() {
        let tmp = TestTemp::new();
        let source = tmp.path().join("source");
        let dest = tmp.path().join("dest");
        fs::create_dir_all(&source).unwrap();
        fs::create_dir_all(&dest).unwrap();
        fs::write(source.join("current.txt"), "new\n").unwrap();
        fs::write(dest.join("stale.txt"), "old\n").unwrap();

        replace_tree_preserving_metadata(&source, &dest).unwrap();

        assert_eq!(
            fs::read_to_string(dest.join("current.txt")).unwrap(),
            "new\n"
        );
        assert!(!dest.join("stale.txt").exists());
    }

    #[test]
    fn guest_payload_commit_script_swaps_temp_paths_into_live_paths() {
        let script = guest_commit_payload_script(
            &[(
                "/var/lib/proxnix/build-input",
                "/var/lib/proxnix/.build-input.proxnix-new",
            )],
            &[(
                "/var/lib/proxnix/runtime/vmid",
                "/var/lib/proxnix/runtime/.vmid.proxnix-new",
            )],
        );

        assert!(script.contains(
            "swap_dir /var/lib/proxnix/build-input /var/lib/proxnix/.build-input.proxnix-new"
        ));
        assert!(script.contains(
            "mv -f /var/lib/proxnix/runtime/.vmid.proxnix-new /var/lib/proxnix/runtime/vmid"
        ));
        assert!(script.contains("proxnix-old-$$"));
    }

    #[test]
    fn golden_template_build_warms_and_protects_local_store() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let old_args = env::var_os("PROXNIX_NIX_ARGS_FILE");
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let authority = root.join("authority");
        let pve = tmp.path().join("pve/lxc");
        let fake_bin = tmp.path().join("bin");
        let gcroots = root.join("gcroots/deploy");
        let nix_args = tmp.path().join("nix-args");
        fs::create_dir_all(&fake_bin).unwrap();
        fs::create_dir_all(&pve).unwrap();
        fs::create_dir_all(&root).unwrap();
        write_executable(
            &fake_bin.join("proxnix-authority-render"),
            fake_authority_render(),
        );
        write_executable(
            &fake_bin.join("nix"),
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$PROXNIX_NIX_ARGS_FILE\"\nout_link=\"\"\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    --out-link) out_link=\"$2\"; shift 2;;\n    *) shift;;\n  esac\ndone\n[ -n \"$out_link\" ] && { mkdir -p \"$(dirname \"$out_link\")\"; ln -sfn /nix/store/golden-template-system \"$out_link\"; }\nprintf '%s\\n' /nix/store/golden-template-system\n",
        );
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
        env::set_var("PROXNIX_NIX_ARGS_FILE", &nix_args);

        build_golden(
            &BuildGoldenConfig {
                root: root.clone(),
                authority: authority.clone(),
                pve_lxc_dir: pve,
                gcroot_dir: gcroots.clone(),
                authority_render: Some(fake_bin.join("proxnix-authority-render")),
            },
            &BuildGoldenOptions {
                node_name: "pve1".to_owned(),
            },
        )
        .unwrap();

        assert!(fs::read_to_string(nix_args)
            .unwrap()
            .contains("#nixosConfigurations.proxnix-golden-template.config.system.build.toplevel"));
        assert_eq!(
            fs::read_link(gcroots.join("golden-template")).unwrap(),
            PathBuf::from("/nix/store/golden-template-system")
        );
        restore_env("PATH", old_path);
        restore_env("PROXNIX_NIX_ARGS_FILE", old_args);
    }

    #[test]
    fn golden_template_build_uses_published_lock_when_present() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let old_args = env::var_os("PROXNIX_NIX_ARGS_FILE");
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let authority = root.join("authority");
        let fake_bin = tmp.path().join("bin");
        let nix_args = tmp.path().join("nix-args");
        fs::create_dir_all(&fake_bin).unwrap();
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("flake.lock"), "{\"nodes\":{}}\n").unwrap();
        write_executable(
            &fake_bin.join("proxnix-authority-render"),
            fake_authority_render(),
        );
        write_executable(
            &fake_bin.join("nix"),
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$PROXNIX_NIX_ARGS_FILE\"\nprintf '%s\\n' /nix/store/golden-template-system\n",
        );
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
        env::set_var("PROXNIX_NIX_ARGS_FILE", &nix_args);

        build_golden(
            &BuildGoldenConfig {
                root: root.clone(),
                authority: authority.clone(),
                pve_lxc_dir: tmp.path().join("pve/lxc"),
                gcroot_dir: root.join("gcroots/deploy"),
                authority_render: Some(fake_bin.join("proxnix-authority-render")),
            },
            &BuildGoldenOptions {
                node_name: "pve1".to_owned(),
            },
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(authority.join("flake.lock")).unwrap(),
            "{\"nodes\":{}}\n"
        );
        assert!(!fs::read_to_string(nix_args)
            .unwrap()
            .contains("--no-write-lock-file"));
        restore_env("PATH", old_path);
        restore_env("PROXNIX_NIX_ARGS_FILE", old_args);
    }

    fn restore_env(name: &str, value: Option<std::ffi::OsString>) {
        if let Some(value) = value {
            env::set_var(name, value);
        } else {
            env::remove_var(name);
        }
    }

    #[test]
    fn seed_dispatch_preserves_running_seed_arguments() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let tmp = TestTemp::new();
        let fake_bin = tmp.path().join("bin");
        fs::create_dir_all(&fake_bin).unwrap();
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

        let action = seed_action(&[
            "--vmid".to_owned(),
            "101".to_owned(),
            "--node-name".to_owned(),
            "pve1".to_owned(),
        ])
        .unwrap();

        assert_eq!(
            action,
            SeedAction::Running(vec![
                "--vmid".to_owned(),
                "101".to_owned(),
                "--node-name".to_owned(),
                "pve1".to_owned()
            ])
        );
        restore_env("PATH", old_path);
    }

    #[test]
    fn seed_dispatch_uses_offline_seed_when_rootfs_is_passed() {
        let tmp = TestTemp::new();
        let rootfs = tmp.path().join("rootfs");
        fs::create_dir_all(rootfs.join("etc")).unwrap();

        let action = seed_action(&[
            "--vmid".to_owned(),
            "101".to_owned(),
            "--rootfs".to_owned(),
            rootfs.display().to_string(),
        ])
        .unwrap();

        assert_eq!(
            action,
            SeedAction::Offline(SeedOfflineOptions {
                vmid: "101".to_owned(),
                rootfs,
            })
        );
    }

    #[test]
    fn seed_dispatch_rejects_stopped_running_seed_target() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let tmp = TestTemp::new();
        let fake_bin = tmp.path().join("bin");
        fs::create_dir_all(&fake_bin).unwrap();
        write_executable(
            &fake_bin.join("pct"),
            "#!/bin/sh\n[ \"$1\" = status ] || exit 2\nprintf '%s\\n' 'status: stopped'\n",
        );
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

        let error = seed_action(&["--vmid".to_owned(), "101".to_owned()]).unwrap_err();

        assert!(error.contains("VMID 101 is stopped; pass --rootfs"));
        restore_env("PATH", old_path);
    }

    fn fake_nix_copy() -> &'static str {
        "#!/bin/sh\nif [ \"$1\" != \"copy\" ] || [ \"$3\" != \"--to\" ]; then exit 2; fi\nroot=\"${4#local?root=}\"\nsystem=\"$5\"\nmkdir -p \"${root}${system}/bin\"\nprintf '#!/bin/sh\\nexit 0\\n' > \"${root}${system}/bin/switch-to-configuration\"\nchmod +x \"${root}${system}/bin/switch-to-configuration\"\nprintf '#!/bin/sh\\nexit 0\\n' > \"${root}${system}/init\"\nchmod +x \"${root}${system}/init\"\n"
    }

    #[test]
    fn seed_offline_copies_to_rootfs_and_sets_boot_profile() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let tmp = TestTemp::new();
        let root = tmp.path().join("proxnix");
        let rootfs = tmp.path().join("rootfs");
        let fake_bin = tmp.path().join("bin");
        let status_dir = root.join("status");
        fs::create_dir_all(&fake_bin).unwrap();
        fs::create_dir_all(&status_dir).unwrap();
        fs::create_dir_all(rootfs.join("etc")).unwrap();
        fs::create_dir_all(rootfs.join("sbin")).unwrap();
        fs::write(rootfs.join("sbin/init"), "# old concrete NixOS LXC init\n").unwrap();
        fs::write(
            status_dir.join("101.json"),
            serde_json::to_string(&json!({
                "vmid": 101,
                "hostname": "ct101",
                "desired_system": "/nix/store/built-system-101",
                "current_system": "/nix/store/old-system-101",
                "previous_system": null,
                "lastBuildStatus": "ok",
                "lastDeployStatus": "not-run",
                "lastError": null,
            }))
            .unwrap(),
        )
        .unwrap();
        write_executable(&fake_bin.join("nix"), fake_nix_copy());
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

        seed_offline(
            &SeedOfflineConfig {
                status_dir: status_dir.clone(),
            },
            &SeedOfflineOptions {
                vmid: "101".to_owned(),
                rootfs: rootfs.clone(),
            },
        )
        .unwrap();

        let runtime = rootfs.join("var/lib/proxnix/runtime");
        assert_eq!(
            fs::read_to_string(runtime.join("next-system"))
                .unwrap()
                .trim(),
            "/nix/store/built-system-101"
        );
        assert_eq!(
            fs::read_to_string(runtime.join("previous-system"))
                .unwrap()
                .trim(),
            "/nix/store/old-system-101"
        );
        let profiles = rootfs.join("nix/var/nix/profiles");
        let system_profile = profiles.join("system");
        assert!(system_profile.is_symlink());
        let generation_link = profiles.join(fs::read_link(&system_profile).unwrap());
        assert!(generation_link.is_symlink());
        assert_eq!(
            fs::read_link(generation_link).unwrap(),
            PathBuf::from("/nix/store/built-system-101")
        );
        assert_eq!(
            fs::read_link(rootfs.join("sbin/init")).unwrap(),
            PathBuf::from("/nix/var/nix/profiles/system/init")
        );
        assert!(rootfs.join("nix/var/nix/gcroots/profiles").is_symlink());
        let status = read_status_object(&status_dir.join("101.json")).unwrap();
        assert_eq!(
            status["lastDeployStatus"],
            Value::String("offline-seeded".to_owned())
        );
        assert_eq!(status["container_has_closure"], Value::Bool(true));
        restore_env("PATH", old_path);
    }

    #[test]
    fn seed_offline_records_guest_activation_marker_without_trusting_it() {
        let tmp = TestTemp::new();
        let rootfs = tmp.path().join("rootfs");
        let status_dir = tmp.path().join("proxnix/status");
        let runtime = rootfs.join("var/lib/proxnix/runtime");
        fs::create_dir_all(&status_dir).unwrap();
        fs::create_dir_all(rootfs.join("etc")).unwrap();
        fs::create_dir_all(&runtime).unwrap();
        fs::write(
            runtime.join("activated-system"),
            "/nix/store/built-system-101\n",
        )
        .unwrap();
        fs::write(
            status_dir.join("101.json"),
            serde_json::to_string(&json!({
                "vmid": 101,
                "hostname": "ct101",
                "desired_system": "/nix/store/built-system-101",
                "current_system": "/nix/store/old-system-101",
                "previous_system": "/nix/store/old-system-101",
                "lastBuildStatus": "failed",
                "lastDeployStatus": "offline-seeded",
                "lastError": null,
            }))
            .unwrap(),
        )
        .unwrap();

        seed_offline(
            &SeedOfflineConfig {
                status_dir: status_dir.clone(),
            },
            &SeedOfflineOptions {
                vmid: "101".to_owned(),
                rootfs,
            },
        )
        .unwrap();

        let status = read_status_object(&status_dir.join("101.json")).unwrap();
        assert_eq!(
            status["current_system"],
            Value::String("/nix/store/old-system-101".to_owned())
        );
        assert_eq!(
            status["lastDeployStatus"],
            Value::String("offline-seeded".to_owned())
        );
        assert_eq!(
            status["guest_activated_system"],
            Value::String("/nix/store/built-system-101".to_owned())
        );
        assert_eq!(status["guestActivationMarkerDrift"], Value::Bool(true));
    }

    #[test]
    fn seed_offline_repairs_profile_even_when_status_is_current() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let tmp = TestTemp::new();
        let rootfs = tmp.path().join("rootfs");
        let status_dir = tmp.path().join("proxnix/status");
        let fake_bin = tmp.path().join("bin");
        fs::create_dir_all(&fake_bin).unwrap();
        fs::create_dir_all(&status_dir).unwrap();
        fs::create_dir_all(rootfs.join("etc")).unwrap();
        fs::write(
            status_dir.join("101.json"),
            serde_json::to_string(&json!({
                "vmid": 101,
                "hostname": "ct101",
                "desired_system": "/nix/store/built-system-101",
                "current_system": "/nix/store/built-system-101",
                "previous_system": null,
                "lastBuildStatus": "ok",
                "lastDeployStatus": "not-run",
            }))
            .unwrap(),
        )
        .unwrap();
        write_executable(&fake_bin.join("nix"), fake_nix_copy());
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

        seed_offline(
            &SeedOfflineConfig {
                status_dir: status_dir.clone(),
            },
            &SeedOfflineOptions {
                vmid: "101".to_owned(),
                rootfs: rootfs.clone(),
            },
        )
        .unwrap();

        let profiles = rootfs.join("nix/var/nix/profiles");
        let generation_link = profiles.join(fs::read_link(profiles.join("system")).unwrap());
        assert_eq!(
            fs::read_link(generation_link).unwrap(),
            PathBuf::from("/nix/store/built-system-101")
        );
        restore_env("PATH", old_path);
    }
}
