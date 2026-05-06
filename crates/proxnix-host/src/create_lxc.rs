use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::UNIX_EPOCH;

use crate::common::{
    effective_uid, find_in_path, remove_path_if_exists, take_arg, valid_vmid, HostResult,
};

#[derive(Debug, Clone, PartialEq, Eq)]
struct Options {
    vmid: String,
    hostname: String,
    template: String,
    storage: String,
    disk: String,
    memory: String,
    swap: String,
    cores: String,
    bridge: String,
    ip_config: String,
    gateway: String,
    ssh_public_keys: String,
    unprivileged: String,
    start_after_create: bool,
    create_config_dir: bool,
    dry_run: bool,
    assume_yes: bool,
    template_auto: bool,
    storage_auto: bool,
    cleanup_existing: bool,
    existing_hostname: String,
    skip_doctor: bool,
    help: bool,
}

#[derive(Debug, Clone)]
struct Bins {
    pct: PathBuf,
    pveversion: PathBuf,
    pveam: PathBuf,
    pvesm: PathBuf,
    pvesh: Option<PathBuf>,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            vmid: String::new(),
            hostname: String::new(),
            template: String::new(),
            storage: String::new(),
            disk: "8".to_owned(),
            memory: "2048".to_owned(),
            swap: "512".to_owned(),
            cores: "2".to_owned(),
            bridge: "vmbr0".to_owned(),
            ip_config: "dhcp".to_owned(),
            gateway: String::new(),
            ssh_public_keys: String::new(),
            unprivileged: "1".to_owned(),
            start_after_create: true,
            create_config_dir: true,
            dry_run: false,
            assume_yes: false,
            template_auto: false,
            storage_auto: false,
            cleanup_existing: false,
            existing_hostname: String::new(),
            skip_doctor: false,
            help: false,
        }
    }
}

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let mut options = parse_args(args)?;
    if options.help {
        usage();
        return Ok(());
    }

    let bins = Bins::init()?;
    if effective_uid() != 0 {
        return Err("must be run as root".into());
    }
    if !bins.pveversion.is_file() {
        return Err("pveversion not found - is this a Proxmox host?".into());
    }
    if !bins.pct.is_file() {
        return Err("pct not found - is this a Proxmox host?".into());
    }

    msg_info("Checking proxnix installation and local Proxmox defaults");
    auto_detect_storage_if_needed(&mut options, &bins)?;
    auto_detect_template_if_needed(&mut options, &bins)?;
    prompt_for_missing_values(&mut options, &bins)?;
    validate_inputs(&options)?;
    inspect_existing_container(&mut options, &bins)?;
    if options.storage_auto {
        msg_ok(&format!("Using rootfs storage: {}", options.storage));
    }
    if options.template_auto {
        msg_ok(&format!(
            "Using newest local NixOS template: {}",
            options.template
        ));
    }
    if !options.skip_doctor {
        run_proxnix_check(&bins)?;
    }
    print_plan(&options);
    confirm_plan(&options)?;
    cleanup_existing_container_if_requested(&options, &bins)?;
    create_container(&options, &bins)?;
    create_host_side_dirs(&options)?;
    start_container_if_requested(&options, &bins)?;
    print_next_steps(&options);
    Ok(())
}

impl Bins {
    fn init() -> Result<Self, String> {
        Ok(Self {
            pct: pick_bin(&["/usr/sbin/pct", "/usr/bin/pct", "pct"])
                .ok_or("pct not found - is this a Proxmox host?")?,
            pveversion: pick_bin(&["/usr/sbin/pveversion", "/usr/bin/pveversion", "pveversion"])
                .ok_or("pveversion not found - is this a Proxmox host?")?,
            pveam: pick_bin(&["/usr/sbin/pveam", "/usr/bin/pveam", "pveam"])
                .ok_or("pveam not found - is this a Proxmox host?")?,
            pvesm: pick_bin(&["/usr/sbin/pvesm", "/usr/bin/pvesm", "pvesm"])
                .ok_or("pvesm not found - is this a Proxmox host?")?,
            pvesh: pick_bin(&["/usr/bin/pvesh", "/usr/sbin/pvesh", "pvesh"]),
        })
    }
}

fn pick_bin(candidates: &[&str]) -> Option<PathBuf> {
    for candidate in candidates {
        let path = if candidate.contains('/') {
            let path = PathBuf::from(candidate);
            path.exists().then_some(path)
        } else {
            find_in_path(candidate)
        };
        if let Some(path) = path {
            return Some(path);
        }
    }
    None
}

fn usage() {
    println!(
        "\
Usage:
  proxnix-host create-lxc [options]

Required:
  --vmid ID                 Numeric container VMID
  --hostname NAME           Container hostname

Optional:
  --template STORAGE:FILE   Proxmox LXC template reference
                            (omit to auto-detect the newest local NixOS template)
  --storage STORAGE         Rootfs storage name used with --disk
                            (omit to auto-detect a rootdir-capable storage)
  --disk GB                 Rootfs size in GB (default: 8)
  --memory MB               RAM in MB (default: 2048)
  --swap MB                 Swap in MB (default: 512)
  --cores N                 CPU cores (default: 2)
  --bridge NAME             Network bridge (default: vmbr0)
  --ip CIDR|dhcp            IP config for net0 (default: dhcp)
  --gw ADDRESS              Gateway for static IP config
  --ssh-public-keys PATH    SSH public keys file for the CT root account
  --unprivileged 0|1        Create privileged or unprivileged CT (default: 1)
  --start                   Start the CT after creation (default)
  --no-start                Create the CT but do not start it
  --no-config-dir           Skip creating /var/lib/proxnix/containers/<vmid>/
  --no-doctor               Skip proxnix-doctor --host-only (for reconciler use)
  --cleanup-existing        Replace an existing CT only when its hostname already matches --hostname
  --dry-run                 Print the commands without running them
  --yes                     Skip the confirmation prompt
  --help                    Show this help
"
    );
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut options = Options::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => options.vmid = take_arg(args, &mut index, "--vmid")?,
            "--hostname" => options.hostname = take_arg(args, &mut index, "--hostname")?,
            "--template" => options.template = take_arg(args, &mut index, "--template")?,
            "--storage" => options.storage = take_arg(args, &mut index, "--storage")?,
            "--disk" => options.disk = take_arg(args, &mut index, "--disk")?,
            "--memory" => options.memory = take_arg(args, &mut index, "--memory")?,
            "--swap" => options.swap = take_arg(args, &mut index, "--swap")?,
            "--cores" => options.cores = take_arg(args, &mut index, "--cores")?,
            "--bridge" => options.bridge = take_arg(args, &mut index, "--bridge")?,
            "--ip" => options.ip_config = take_arg(args, &mut index, "--ip")?,
            "--gw" => options.gateway = take_arg(args, &mut index, "--gw")?,
            "--ssh-public-keys" => {
                options.ssh_public_keys = take_arg(args, &mut index, "--ssh-public-keys")?
            }
            "--unprivileged" => {
                options.unprivileged = take_arg(args, &mut index, "--unprivileged")?
            }
            "--start" => options.start_after_create = true,
            "--no-start" => options.start_after_create = false,
            "--no-config-dir" => options.create_config_dir = false,
            "--no-doctor" => options.skip_doctor = true,
            "--cleanup-existing" => options.cleanup_existing = true,
            "--dry-run" => options.dry_run = true,
            "--yes" => options.assume_yes = true,
            "--help" | "-h" => options.help = true,
            other => return Err(format!("unknown argument: {other}")),
        }
        index += 1;
    }
    Ok(options)
}

fn auto_detect_storage_if_needed(options: &mut Options, bins: &Bins) -> Result<(), String> {
    if matches!(options.storage.as_str(), "" | "auto" | "latest") {
        options.storage = detect_rootfs_storage(bins)?;
        options.storage_auto = true;
    }
    Ok(())
}

fn detect_rootfs_storage(bins: &Bins) -> Result<String, String> {
    let output = command_output(Command::new(&bins.pvesm).args(["status", "--content", "rootdir"]))
        .map_err(|err| format!("failed to inspect Proxmox storage: {err}"))?;
    let storages = output
        .lines()
        .skip(1)
        .filter_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            (fields.len() > 2 && fields[2] == "active").then(|| fields[0].to_owned())
        })
        .collect::<Vec<_>>();

    if storages.is_empty() {
        return Err(
            "could not auto-detect a rootdir-capable active storage; pass --storage explicitly"
                .to_owned(),
        );
    }
    for preferred in ["local-lvm", "local-zfs"] {
        if storages.iter().any(|storage| storage == preferred) {
            return Ok(preferred.to_owned());
        }
    }
    Ok(storages[0].clone())
}

fn auto_detect_template_if_needed(options: &mut Options, bins: &Bins) -> Result<(), String> {
    if matches!(options.template.as_str(), "" | "auto" | "latest") {
        options.template = detect_latest_template(bins)?;
        options.template_auto = true;
    }
    Ok(())
}

fn detect_latest_template(bins: &Bins) -> Result<String, String> {
    let storage_output =
        command_output(Command::new(&bins.pvesm).args(["status", "--content", "vztmpl"]))
            .map_err(|err| format!("failed to inspect Proxmox template storage: {err}"))?;
    let storages = storage_output
        .lines()
        .skip(1)
        .filter_map(|line| line.split_whitespace().next().map(str::to_owned))
        .collect::<Vec<_>>();

    let mut best: Option<(String, i64)> = None;
    for storage in storages {
        let Ok(output) = command_output(Command::new(&bins.pveam).args(["list", &storage])) else {
            continue;
        };
        for candidate in output
            .lines()
            .skip(1)
            .filter_map(|line| line.split_whitespace().next())
        {
            let lower = candidate.to_ascii_lowercase();
            if !lower.contains("nixos") || !lower.contains(".tar.") {
                continue;
            }
            let volid = normalize_template_volid(&storage, candidate);
            let mtime = template_mtime(bins, &volid);
            if best.as_ref().is_none_or(|(best_volid, best_mtime)| {
                mtime > *best_mtime || (mtime == *best_mtime && volid > *best_volid)
            }) {
                best = Some((volid, mtime));
            }
        }
    }
    best.map(|(volid, _)| volid).ok_or_else(|| {
        "could not auto-detect a local NixOS template; pass --template explicitly or download one first with pveam".to_owned()
    })
}

fn normalize_template_volid(storage: &str, candidate: &str) -> String {
    if candidate.contains(':') {
        return candidate.to_owned();
    }
    let name = Path::new(candidate)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(candidate);
    format!("{storage}:vztmpl/{name}")
}

fn template_mtime(bins: &Bins, volid: &str) -> i64 {
    let Ok(path) = command_output(Command::new(&bins.pvesm).args(["path", volid])) else {
        return 0;
    };
    fs::metadata(path.trim())
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| modified.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn next_vmid_default(bins: &Bins) -> String {
    let Some(pvesh) = &bins.pvesh else {
        return String::new();
    };
    command_output(Command::new(pvesh).args(["get", "/cluster/nextid"]))
        .unwrap_or_default()
        .trim()
        .to_owned()
}

fn prompt_for_missing_values(options: &mut Options, bins: &Bins) -> Result<(), String> {
    let default_vmid = next_vmid_default(bins);
    prompt_if_empty(&mut options.vmid, "VMID", &default_vmid)?;
    prompt_if_empty(&mut options.hostname, "Hostname", "")?;
    let disk_default = options.disk.clone();
    prompt_if_empty(&mut options.disk, "Disk size in GB", &disk_default)?;
    let memory_default = options.memory.clone();
    prompt_if_empty(&mut options.memory, "Memory in MB", &memory_default)?;
    let swap_default = options.swap.clone();
    prompt_if_empty(&mut options.swap, "Swap in MB", &swap_default)?;
    let cores_default = options.cores.clone();
    prompt_if_empty(&mut options.cores, "CPU cores", &cores_default)?;
    let bridge_default = options.bridge.clone();
    prompt_if_empty(&mut options.bridge, "Bridge", &bridge_default)?;
    let ip_default = options.ip_config.clone();
    prompt_if_empty(
        &mut options.ip_config,
        "IP config (dhcp or CIDR)",
        &ip_default,
    )?;
    if options.ip_config != "dhcp" {
        prompt_if_empty(&mut options.gateway, "Gateway", "")?;
    }
    Ok(())
}

fn prompt_if_empty(value: &mut String, prompt: &str, default: &str) -> Result<(), String> {
    if !value.is_empty() {
        return Ok(());
    }
    if default.is_empty() {
        print!("{prompt}: ");
    } else {
        print!("{prompt} [{default}]: ");
    }
    io::stdout()
        .flush()
        .map_err(|err| format!("failed to flush prompt: {err}"))?;
    let mut input = String::new();
    io::stdin()
        .read_line(&mut input)
        .map_err(|err| format!("failed to read prompt: {err}"))?;
    let input = input.trim();
    *value = if input.is_empty() {
        default.to_owned()
    } else {
        input.to_owned()
    };
    Ok(())
}

fn validate_inputs(options: &Options) -> Result<(), String> {
    if !valid_vmid(&options.vmid) {
        return Err("--vmid must be numeric".to_owned());
    }
    if options.hostname.is_empty() {
        return Err("--hostname is required".to_owned());
    }
    if options.storage.is_empty() {
        return Err("--storage is required".to_owned());
    }
    if options
        .disk
        .parse::<f64>()
        .ok()
        .filter(|value| *value >= 0.0)
        .is_none()
    {
        return Err("--disk must be a size in GB".to_owned());
    }
    for (flag, value) in [
        ("--memory", &options.memory),
        ("--swap", &options.swap),
        ("--cores", &options.cores),
    ] {
        if !valid_vmid(value) {
            return Err(format!("{flag} must be numeric"));
        }
    }
    if !matches!(options.unprivileged.as_str(), "0" | "1") {
        return Err("--unprivileged must be 0 or 1".to_owned());
    }
    if !options.gateway.is_empty() && options.ip_config == "dhcp" {
        return Err("--gw requires a static --ip value".to_owned());
    }
    if !options.ssh_public_keys.is_empty() && !Path::new(&options.ssh_public_keys).is_file() {
        return Err(format!(
            "--ssh-public-keys file not found: {}",
            options.ssh_public_keys
        ));
    }
    Ok(())
}

fn run_proxnix_check(bins: &Bins) -> Result<(), String> {
    let doctor = resolve_doctor()?;
    println!();
    println!("Checking proxnix installation...");
    let status = Command::new(&doctor)
        .arg("--host-only")
        .status()
        .map_err(|err| format!("failed to run {}: {err}", doctor.display()))?;
    if !status.success() {
        return Err("proxnix host checks failed. This helper does not install proxnix; fix the existing install and re-run.".to_owned());
    }
    let _ = bins;
    Ok(())
}

fn resolve_doctor() -> Result<PathBuf, String> {
    if Path::new("/usr/local/sbin/proxnix-doctor").is_file() {
        return Ok(PathBuf::from("/usr/local/sbin/proxnix-doctor"));
    }
    if let Some(path) = env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join("proxnix-doctor")))
        .filter(|path| path.is_file())
    {
        return Ok(path);
    }
    Err(
        "proxnix-doctor is not available. Install proxnix separately before using this helper."
            .to_owned(),
    )
}

fn existing_container_config_path(vmid: &str) -> PathBuf {
    PathBuf::from(format!("/etc/pve/lxc/{vmid}.conf"))
}

fn host_container_dir(vmid: &str) -> PathBuf {
    PathBuf::from(format!("/var/lib/proxnix/containers/{vmid}"))
}

fn host_private_container_dir(vmid: &str) -> PathBuf {
    PathBuf::from(format!("/var/lib/proxnix/private/containers/{vmid}"))
}

fn host_dropins_dir(vmid: &str) -> PathBuf {
    host_container_dir(vmid).join("dropins")
}

fn existing_container_hostname(vmid: &str, bins: &Bins) -> Result<String, String> {
    let output = command_output(Command::new(&bins.pct).args(["config", vmid]))
        .map_err(|err| format!("failed to inspect existing container {vmid}: {err}"))?;
    Ok(output
        .lines()
        .find_map(|line| line.strip_prefix("hostname: ").map(str::to_owned))
        .unwrap_or_default())
}

fn inspect_existing_container(options: &mut Options, bins: &Bins) -> Result<(), String> {
    if !existing_container_config_path(&options.vmid).is_file() {
        return Ok(());
    }
    let existing_hostname = existing_container_hostname(&options.vmid, bins)?;
    if existing_hostname.is_empty() {
        return Err(format!(
            "container VMID {} already exists but its hostname could not be determined",
            options.vmid
        ));
    }
    if !options.cleanup_existing {
        return Err(format!(
            "container VMID {} already exists with hostname {}; rerun with --cleanup-existing only if you intend to replace it",
            options.vmid, existing_hostname
        ));
    }
    if existing_hostname != options.hostname {
        return Err(format!(
            "container VMID {} already exists with hostname {}; --cleanup-existing only replaces an existing CT when its hostname matches --hostname {}",
            options.vmid, existing_hostname, options.hostname
        ));
    }
    options.existing_hostname = existing_hostname;
    Ok(())
}

fn build_net0(options: &Options) -> String {
    let mut net0 = format!(
        "name=eth0,bridge={},ip={}",
        options.bridge, options.ip_config
    );
    if !options.gateway.is_empty() {
        net0.push_str(&format!(",gw={}", options.gateway));
    }
    net0
}

fn pct_create_args(options: &Options) -> Vec<String> {
    let mut args = vec![
        "create".to_owned(),
        options.vmid.clone(),
        options.template.clone(),
        "--ostype".to_owned(),
        "nixos".to_owned(),
        "--hostname".to_owned(),
        options.hostname.clone(),
        "--rootfs".to_owned(),
        format!("{}:{}", options.storage, options.disk),
        "--memory".to_owned(),
        options.memory.clone(),
        "--swap".to_owned(),
        options.swap.clone(),
        "--cores".to_owned(),
        options.cores.clone(),
        "--net0".to_owned(),
        build_net0(options),
        "--unprivileged".to_owned(),
        options.unprivileged.clone(),
        "--features".to_owned(),
        "nesting=1,keyctl=1".to_owned(),
    ];
    if !options.ssh_public_keys.is_empty() {
        args.push("--ssh-public-keys".to_owned());
        args.push(options.ssh_public_keys.clone());
    }
    args
}

fn print_plan(options: &Options) {
    let template_display = if options.template_auto {
        format!("{} (auto)", options.template)
    } else {
        options.template.clone()
    };
    let storage_display = if options.storage_auto {
        format!("{} (auto)", options.storage)
    } else {
        options.storage.clone()
    };

    println!();
    println!("proxnix NixOS LXC plan");
    println!("======================");
    info(&format!("VMID:           {}", options.vmid));
    info(&format!("Hostname:       {}", options.hostname));
    info(&format!("Template:       {template_display}"));
    info(&format!(
        "Rootfs:         {storage_display}:{}",
        options.disk
    ));
    info(&format!(
        "Memory / swap:  {} / {} MB",
        options.memory, options.swap
    ));
    info(&format!("Cores:          {}", options.cores));
    info(&format!("Bridge:         {}", options.bridge));
    info(&format!("IP config:      {}", options.ip_config));
    if !options.gateway.is_empty() {
        info(&format!("Gateway:        {}", options.gateway));
    }
    info(&format!("Unprivileged:   {}", options.unprivileged));
    info("Nesting:        1 (always enabled)");
    info(&format!(
        "Create config:  {}",
        bool_int(options.create_config_dir)
    ));
    info(&format!(
        "Start after:    {}",
        bool_int(options.start_after_create)
    ));
    if !options.existing_hostname.is_empty() {
        info(&format!(
            "Replace VMID:   yes (existing hostname {})",
            options.existing_hostname
        ));
    }
    if !options.ssh_public_keys.is_empty() {
        info(&format!("SSH keys:       {}", options.ssh_public_keys));
    }
    if options.dry_run {
        info("Mode:           dry-run");
    }
}

fn confirm_plan(options: &Options) -> Result<(), String> {
    if options.assume_yes {
        return Ok(());
    }
    println!();
    print!("Create this NixOS container? [y/N]: ");
    io::stdout()
        .flush()
        .map_err(|err| format!("failed to flush prompt: {err}"))?;
    let mut answer = String::new();
    io::stdin()
        .read_line(&mut answer)
        .map_err(|err| format!("failed to read confirmation: {err}"))?;
    match answer.trim() {
        "y" | "Y" | "yes" | "YES" => Ok(()),
        _ => Err("aborted by user".to_owned()),
    }
}

fn cleanup_existing_container_if_requested(options: &Options, bins: &Bins) -> Result<(), String> {
    if options.existing_hostname.is_empty() {
        return Ok(());
    }
    msg_info(&format!(
        "Cleaning up existing container {} with matching hostname {}",
        options.vmid, options.existing_hostname
    ));
    if pct_status(&options.vmid, bins) == "running" {
        run_cmd(
            options,
            Command::new(&bins.pct).args(["stop", &options.vmid]),
        )?;
    }
    run_cmd(
        options,
        Command::new(&bins.pct).args(["destroy", &options.vmid]),
    )?;
    remove_host_path(options, &host_container_dir(&options.vmid))?;
    remove_host_path(options, &host_private_container_dir(&options.vmid))?;
    if !options.dry_run && existing_container_config_path(&options.vmid).exists() {
        return Err(format!(
            "existing container {} still exists after cleanup",
            options.vmid
        ));
    }
    msg_ok(&format!("Removed existing container {}", options.vmid));
    Ok(())
}

fn create_container(options: &Options, bins: &Bins) -> Result<(), String> {
    msg_info(&format!("Creating container {}", options.vmid));
    let mut command = Command::new(&bins.pct);
    command.args(pct_create_args(options));
    run_cmd(options, &mut command)?;
    verify_container_created(options)?;
    msg_ok(&format!("Created container {}", options.vmid));
    Ok(())
}

fn create_host_side_dirs(options: &Options) -> Result<(), String> {
    if !options.create_config_dir {
        return Ok(());
    }
    msg_info(&format!(
        "Creating proxnix config directories for {}",
        options.vmid
    ));
    create_host_dir(options, &host_dropins_dir(&options.vmid))?;
    verify_host_side_dirs(options)?;
    msg_ok(&format!(
        "Created host-side proxnix directories for {}",
        options.vmid
    ));
    Ok(())
}

fn start_container_if_requested(options: &Options, bins: &Bins) -> Result<(), String> {
    if !options.start_after_create {
        return Ok(());
    }
    msg_info(&format!("Starting container {}", options.vmid));
    run_cmd(
        options,
        Command::new(&bins.pct).args(["start", &options.vmid]),
    )?;
    verify_container_started(options, bins)?;
    msg_ok(&format!("Started container {}", options.vmid));
    Ok(())
}

fn verify_container_created(options: &Options) -> Result<(), String> {
    if options.dry_run || existing_container_config_path(&options.vmid).is_file() {
        Ok(())
    } else {
        Err(format!(
            "pct create returned successfully, but /etc/pve/lxc/{}.conf was not created",
            options.vmid
        ))
    }
}

fn verify_host_side_dirs(options: &Options) -> Result<(), String> {
    if options.dry_run || !options.create_config_dir || host_dropins_dir(&options.vmid).is_dir() {
        Ok(())
    } else {
        Err(format!(
            "host-side dropins dir was not created for VMID {}",
            options.vmid
        ))
    }
}

fn verify_container_started(options: &Options, bins: &Bins) -> Result<(), String> {
    if options.dry_run
        || !options.start_after_create
        || pct_status(&options.vmid, bins) == "running"
    {
        Ok(())
    } else {
        Err(format!(
            "container {} was created, but failed to start",
            options.vmid
        ))
    }
}

fn pct_status(vmid: &str, bins: &Bins) -> String {
    command_output(Command::new(&bins.pct).args(["status", vmid]))
        .ok()
        .and_then(|output| output.split_whitespace().nth(1).map(str::to_owned))
        .unwrap_or_default()
}

fn print_next_steps(options: &Options) {
    println!();
    println!("Done.");
    println!();
    println!("Next steps:");
    println!("  1. Watch the automatic first-boot apply finish:");
    println!(
        "       pct exec {} -- /run/current-system/sw/bin/journalctl -u proxnix-apply-config.service -b -f",
        options.vmid
    );
    println!(
        "       # If it ever needs a manual retry: pct enter {}",
        options.vmid
    );
    println!("       # then run /root/proxnix-bootstrap.sh");
    println!();
    println!("  2. Add or update secrets for this container at any time:");
    println!("       proxnix-secrets set {} mysecret", options.vmid);
    println!("       proxnix-publish");
    println!("       # restart the CT after publishing updated relay state");
    println!();
    println!("  3. Re-run health checks:");
    match resolve_doctor() {
        Ok(doctor) => println!("       {} {}", doctor.display(), options.vmid),
        Err(_) => println!("       proxnix-doctor {}", options.vmid),
    }
}

fn command_output(command: &mut Command) -> io::Result<String> {
    let output = command.stderr(Stdio::null()).output()?;
    if !output.status.success() {
        return Err(io::Error::other("command failed"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn create_host_dir(options: &Options, path: &Path) -> Result<(), String> {
    if options.dry_run {
        print_dry_run_command("mkdir", [std::ffi::OsStr::new("-p"), path.as_os_str()]);
        return Ok(());
    }
    fs::create_dir_all(path).map_err(|err| format!("failed to create {}: {err}", path.display()))
}

fn remove_host_path(options: &Options, path: &Path) -> Result<(), String> {
    if options.dry_run {
        print_dry_run_command("rm", [std::ffi::OsStr::new("-rf"), path.as_os_str()]);
        return Ok(());
    }
    remove_path_if_exists(path).map_err(|err| format!("failed to remove {}: {err}", path.display()))
}

fn run_cmd(options: &Options, command: &mut Command) -> Result<(), String> {
    if options.dry_run {
        print_dry_run_command(command.get_program(), command.get_args());
        return Ok(());
    }
    let status = command.status().map_err(|err| {
        format!(
            "failed to run {}: {err}",
            command.get_program().to_string_lossy()
        )
    })?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "command failed: {}",
            command.get_program().to_string_lossy()
        ))
    }
}

fn print_dry_run_command<I, S>(program: S, args: I)
where
    I: IntoIterator,
    I::Item: AsRef<std::ffi::OsStr>,
    S: AsRef<std::ffi::OsStr>,
{
    print!("+");
    print!(" {}", shell_quote(&program.as_ref().to_string_lossy()));
    for arg in args {
        print!(" {}", shell_quote(&arg.as_ref().to_string_lossy()));
    }
    println!();
}

fn shell_quote(value: &str) -> String {
    if value.bytes().all(|byte| {
        byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b'/' | b':' | b'=')
    }) {
        return value.to_owned();
    }
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn msg_info(message: &str) {
    println!(" -> {message}");
}

fn msg_ok(message: &str) {
    println!(" OK {message}");
}

fn info(message: &str) {
    println!("  {message}");
}

fn bool_int(value: bool) -> u8 {
    if value {
        1
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_create_lxc_options() {
        let options = parse_args(&[
            "--vmid".to_owned(),
            "101".to_owned(),
            "--hostname".to_owned(),
            "ct101".to_owned(),
            "--storage".to_owned(),
            "local-lvm".to_owned(),
            "--template".to_owned(),
            "local:vztmpl/nixos.tar.xz".to_owned(),
            "--disk".to_owned(),
            "16".to_owned(),
            "--memory".to_owned(),
            "4096".to_owned(),
            "--cores".to_owned(),
            "4".to_owned(),
            "--no-start".to_owned(),
            "--no-doctor".to_owned(),
            "--yes".to_owned(),
        ])
        .unwrap();

        assert_eq!(options.vmid, "101");
        assert_eq!(options.hostname, "ct101");
        assert_eq!(options.storage, "local-lvm");
        assert_eq!(options.template, "local:vztmpl/nixos.tar.xz");
        assert_eq!(options.disk, "16");
        assert_eq!(options.memory, "4096");
        assert_eq!(options.cores, "4");
        assert!(!options.start_after_create);
        assert!(options.skip_doctor);
        assert!(options.assume_yes);
    }

    #[test]
    fn builds_pct_create_arguments() {
        let options = Options {
            vmid: "101".to_owned(),
            hostname: "ct101".to_owned(),
            template: "local:vztmpl/nixos.tar.xz".to_owned(),
            storage: "local-lvm".to_owned(),
            disk: "8".to_owned(),
            memory: "2048".to_owned(),
            swap: "512".to_owned(),
            cores: "2".to_owned(),
            bridge: "vmbr1".to_owned(),
            ip_config: "10.0.0.10/24".to_owned(),
            gateway: "10.0.0.1".to_owned(),
            ssh_public_keys: "/tmp/keys.pub".to_owned(),
            ..Options::default()
        };

        assert_eq!(
            pct_create_args(&options),
            vec![
                "create",
                "101",
                "local:vztmpl/nixos.tar.xz",
                "--ostype",
                "nixos",
                "--hostname",
                "ct101",
                "--rootfs",
                "local-lvm:8",
                "--memory",
                "2048",
                "--swap",
                "512",
                "--cores",
                "2",
                "--net0",
                "name=eth0,bridge=vmbr1,ip=10.0.0.10/24,gw=10.0.0.1",
                "--unprivileged",
                "1",
                "--features",
                "nesting=1,keyctl=1",
                "--ssh-public-keys",
                "/tmp/keys.pub",
            ]
        );
    }

    #[test]
    fn normalizes_template_volids() {
        assert_eq!(
            normalize_template_volid("local", "/var/lib/vz/template/cache/nixos.tar.xz"),
            "local:vztmpl/nixos.tar.xz"
        );
        assert_eq!(
            normalize_template_volid("local", "images:vztmpl/nixos.tar.zst"),
            "images:vztmpl/nixos.tar.zst"
        );
    }

    #[test]
    fn validates_gateway_requires_static_ip() {
        let options = Options {
            vmid: "101".to_owned(),
            hostname: "ct101".to_owned(),
            storage: "local-lvm".to_owned(),
            template: "local:vztmpl/nixos.tar.xz".to_owned(),
            gateway: "10.0.0.1".to_owned(),
            ..Options::default()
        };

        assert_eq!(
            validate_inputs(&options).unwrap_err(),
            "--gw requires a static --ip value"
        );
    }

    #[test]
    fn shell_quote_preserves_safe_args_and_quotes_spaces() {
        assert_eq!(shell_quote("local-lvm:8"), "local-lvm:8");
        assert_eq!(shell_quote("hello world"), "'hello world'");
    }

    #[test]
    fn set_mode_helper_remains_available_for_host_side_files() {
        let tmp = crate::tests::TestTemp::new();
        let path = tmp.path().join("file");
        fs::write(&path, "").unwrap();
        crate::common::set_mode(&path, 0o600).unwrap();
    }
}
