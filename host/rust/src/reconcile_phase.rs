use std::env;
use std::fs;
use std::io;
use std::os::unix::fs::symlink;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{self, Command, Stdio};

use serde_json::{json, Map, Value};

use super::{
    default_node_name, env_path, find_in_path, remove_file_if_exists, remove_path_if_exists,
    render_authority, set_mode, take_arg, utc_now_seconds_z, valid_vmid,
};

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
    authority_render: PathBuf,
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
enum SeedAction {
    Help,
    Offline(SeedOfflineOptions),
    Running(Vec<String>),
}

pub(crate) fn build_golden_main(args: &[String]) -> Result<(), String> {
    let options = parse_build_golden_args(args)?;
    let config = BuildGoldenConfig::from_env();
    build_golden(&config, &options).map_err(|err| format!("build-golden failed: {err}"))
}

pub(crate) fn seed_offline_main(args: &[String]) -> Result<(), String> {
    let options = parse_seed_offline_args(args)?;
    let root = env_path("PROXNIX_DIR", "/var/lib/proxnix");
    let status_dir = env::var_os("PROXNIX_STATUS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("status"));
    seed_offline(&SeedOfflineConfig { status_dir }, &options)
        .map_err(|err| format!("seed-offline failed: {err}"))
}

pub(crate) fn seed_main(args: &[String]) -> Result<(), String> {
    match seed_action(args)? {
        SeedAction::Help => {
            seed_usage();
            Ok(())
        }
        SeedAction::Offline(options) => {
            let root = env_path("PROXNIX_DIR", "/var/lib/proxnix");
            let status_dir = env::var_os("PROXNIX_STATUS_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|| root.join("status"));
            seed_offline(&SeedOfflineConfig { status_dir }, &options)
                .map_err(|err| format!("seed failed: {err}"))
        }
        SeedAction::Running(passthrough_args) => {
            run_reconcile_phase("--seed-only", &passthrough_args)
        }
    }
}

pub(crate) fn build_main(args: &[String]) -> Result<(), String> {
    run_reconcile_phase("--build-only", args)
}

pub(crate) fn activate_main(args: &[String]) -> Result<(), String> {
    run_reconcile_phase("--activate-only", args)
}

fn run_reconcile_phase(flag: &str, args: &[String]) -> Result<(), String> {
    let reconcile = env::var_os("PROXNIX_RECONCILE")
        .map(PathBuf::from)
        .or_else(|| find_in_path("proxnix-reconcile"))
        .unwrap_or_else(|| PathBuf::from("/usr/local/sbin/proxnix-reconcile"));
    let status = Command::new(reconcile)
        .arg(flag)
        .args(args)
        .status()
        .map_err(|err| format!("failed to run proxnix-reconcile: {err}"))?;
    process::exit(status.code().unwrap_or(1));
}

impl BuildGoldenConfig {
    fn from_env() -> Self {
        let root = env_path("PROXNIX_DIR", "/var/lib/proxnix");
        let authority = env::var_os("PROXNIX_AUTHORITY_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("authority"));
        let pve_lxc_dir = env_path("PROXNIX_PVE_LXC_DIR", "/etc/pve/lxc");
        let gcroot_dir = env::var_os("PROXNIX_GCROOT_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("gcroots/deploy"));
        let authority_render = env_path(
            "PROXNIX_AUTHORITY_RENDER",
            "/usr/local/sbin/proxnix-authority-render",
        );
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
            "VMID {vmid} is stopped; pass --rootfs <mounted-rootfs> or let the LXC mount hook run proxnix-reconcile-seed-offline"
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
    if config.authority_render.is_file() {
        let status = Command::new(&config.authority_render)
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
        return Err(io::Error::other("proxnix-authority-render failed"));
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

    let desired_system = status_string(&status, "desiredSystem");
    let current_system = status_string(&status, "currentSystem");
    let previous_system = status_string(&status, "previousSystem");
    let last_build_status = status_string(&status, "lastBuildStatus");

    if desired_system.is_empty() {
        println!("{} offline seed skipped no desired system", options.vmid);
        return Ok(());
    }
    if last_build_status != "ok" {
        println!("{} offline seed skipped build not ok", options.vmid);
        return Ok(());
    }

    let profiles_dir = options.rootfs.join("nix/var/nix/profiles");
    let profile_system =
        profile_target_store_path(&profiles_dir, &profiles_dir.join("system")).unwrap_or_default();
    if current_system == desired_system
        && profile_system == desired_system
        && rootfs_init_points_to_profile(&options.rootfs)
    {
        remove_file_if_exists(&options.rootfs.join("var/lib/proxnix/runtime/next-system"))?;
        println!("{} offline seed skipped current", options.vmid);
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

    let runtime_dir = options.rootfs.join("var/lib/proxnix/runtime");
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

fn record_guest_activation_marker(status: &mut Map<String, Value>, rootfs: &Path) {
    let marker = rootfs.join("var/lib/proxnix/runtime/activated-system");
    let activated = fs::read_to_string(marker)
        .ok()
        .map(|value| value.replace(['\r', '\n'], ""))
        .unwrap_or_default();
    if activated.is_empty() {
        return;
    }
    let desired = status_string(status, "desiredSystem");
    let current = status_string(status, "currentSystem");
    let drift = (!desired.is_empty() && activated != desired)
        || (!current.is_empty() && current != activated);
    status.insert("guestActivatedSystem".to_owned(), json!(activated));
    status.insert(
        "guest_activated_system".to_owned(),
        status["guestActivatedSystem"].clone(),
    );
    status.insert("guestActivationMarkerDrift".to_owned(), json!(drift));
    status.insert("updatedAt".to_owned(), json!(utc_now_seconds_z()));
}

fn mark_offline_seeded(status: &mut Map<String, Value>, desired_system: &str) {
    status.insert("desiredSystem".to_owned(), json!(desired_system));
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

fn rootfs_init_points_to_profile(rootfs: &Path) -> bool {
    let init = rootfs_sbin_dir(rootfs).join("init");
    fs::symlink_metadata(&init)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_symlink())
        && fs::read_link(init).ok().as_deref()
            == Some(Path::new("/nix/var/nix/profiles/system/init"))
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

fn rootfs_path(rootfs: &Path, absolute_path: &str) -> PathBuf {
    rootfs.join(absolute_path.strip_prefix('/').unwrap_or(absolute_path))
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
                authority_render: fake_bin.join("proxnix-authority-render"),
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
                authority_render: fake_bin.join("proxnix-authority-render"),
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
                "desiredSystem": "/nix/store/built-system-101",
                "currentSystem": "/nix/store/old-system-101",
                "previousSystem": null,
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
                "desiredSystem": "/nix/store/built-system-101",
                "currentSystem": "/nix/store/old-system-101",
                "previousSystem": "/nix/store/old-system-101",
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
            status["currentSystem"],
            Value::String("/nix/store/old-system-101".to_owned())
        );
        assert_eq!(
            status["lastDeployStatus"],
            Value::String("offline-seeded".to_owned())
        );
        assert_eq!(
            status["guestActivatedSystem"],
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
                "desiredSystem": "/nix/store/built-system-101",
                "currentSystem": "/nix/store/built-system-101",
                "previousSystem": null,
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
