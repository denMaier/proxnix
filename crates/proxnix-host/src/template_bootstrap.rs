use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::common::{find_in_path, take_arg, HostResult};
use crate::reconcile_phase;
use unix::fcntl::{Flock, FlockArg};

#[derive(Debug, Clone, PartialEq, Eq)]
struct Options {
    template_storage: String,
    template_name: String,
    source_template_name: String,
    rootfs_storage: String,
    temp_vmid: String,
    keep_temp: bool,
    dry_run: bool,
    force: bool,
    help: bool,
}

#[derive(Debug, Clone)]
struct Bins {
    pct: PathBuf,
    pveam: PathBuf,
    pvesm: PathBuf,
    pvesh: PathBuf,
    nix: PathBuf,
    nix_store: PathBuf,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TemplateAction {
    CreateOrRefresh,
    ReuseSharedExisting,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct StorageInfo {
    shared: bool,
    path: Option<PathBuf>,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            template_storage: "local".to_owned(),
            template_name: "proxnix-nixos-golden.tar.xz".to_owned(),
            source_template_name: String::new(),
            rootfs_storage: "auto".to_owned(),
            temp_vmid: String::new(),
            keep_temp: false,
            dry_run: false,
            force: false,
            help: false,
        }
    }
}

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let options = parse_args(args)?;
    if options.help {
        usage();
        return Ok(());
    }
    let bins = Bins::init()?;
    let storage = inspect_storage(&bins, &options.template_storage)?;
    run_with_storage(&bins, &options, &storage)?;
    Ok(())
}

fn run_with_storage(bins: &Bins, options: &Options, storage: &StorageInfo) -> Result<(), String> {
    if storage.shared && !options.dry_run {
        let _lock =
            take_shared_template_lock(storage, &options.template_storage, &options.template_name)?;
        return run_decision(bins, options, storage);
    }
    run_decision(bins, options, storage)
}

fn run_decision(bins: &Bins, options: &Options, storage: &StorageInfo) -> Result<(), String> {
    let exists = template_exists(bins, &options.template_storage, &options.template_name)?;
    match decide_template_action(storage.shared, exists, options.force) {
        TemplateAction::ReuseSharedExisting => {
            println!(
                "shared template storage {} already has {}; reusing existing proxnix golden template",
                options.template_storage, options.template_name
            );
            Ok(())
        }
        TemplateAction::CreateOrRefresh => {
            let plan = BootstrapPlan::resolve(bins, options)?;
            if options.dry_run {
                println!(
                    "would seed host store from {} using temporary VMID {} on rootfs storage {}, then build proxnix golden closure for {} on storage {}{}{}",
                    plan.source_template,
                    plan.temp_vmid,
                    plan.rootfs_storage,
                    options.template_name,
                    options.template_storage,
                    if storage.shared { " (shared)" } else { "" },
                    if options.force { " (force)" } else { "" }
                );
                return Ok(());
            }
            run_bootstrap(bins, options, &plan)?;
            Ok(())
        }
    }
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut options = Options::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--template-storage" => {
                options.template_storage = take_arg(args, &mut index, "--template-storage")?
            }
            "--template-name" => {
                options.template_name = take_arg(args, &mut index, "--template-name")?
            }
            "--source-template-name" => {
                options.source_template_name = take_arg(args, &mut index, "--source-template-name")?
            }
            "--rootfs-storage" => {
                options.rootfs_storage = take_arg(args, &mut index, "--rootfs-storage")?
            }
            "--temp-vmid" => options.temp_vmid = take_arg(args, &mut index, "--temp-vmid")?,
            "--keep-temp" => options.keep_temp = true,
            "--dry-run" => options.dry_run = true,
            "--force" => options.force = true,
            "-h" | "--help" => {
                options.help = true;
            }
            other => return Err(format!("unknown template bootstrap argument: {other}")),
        }
        index += 1;
    }
    Ok(options)
}

fn usage() {
    println!(
        "\
Usage:
  proxnix-host template bootstrap [options]

Options:
  --template-storage NAME  Proxmox template storage (default: local)
  --template-name NAME     proxnix golden template archive name
                           (default: proxnix-nixos-golden.tar.xz)
  --source-template-name NAME
                           Existing NixOS LXC template archive to seed from
                           (default: newest NixOS template on --template-storage)
  --rootfs-storage NAME    Rootfs storage for the temporary offline CT
                           (default: auto)
  --temp-vmid ID           Temporary CT VMID (default: pvesh /cluster/nextid)
  --keep-temp              Keep the temporary CT for debugging
  --force                  Recreate/refresh even when a shared template exists
  --dry-run                Print the cluster-safe creation decision
"
    );
}

impl Bins {
    fn init() -> Result<Self, String> {
        Ok(Self {
            pct: pick_bin(&["/usr/sbin/pct", "/usr/bin/pct", "pct"])
                .ok_or("pct not found - is this a Proxmox host?")?,
            pveam: pick_bin(&["/usr/sbin/pveam", "/usr/bin/pveam", "pveam"])
                .ok_or("pveam not found - is this a Proxmox host?")?,
            pvesm: pick_bin(&["/usr/sbin/pvesm", "/usr/bin/pvesm", "pvesm"])
                .ok_or("pvesm not found - is this a Proxmox host?")?,
            pvesh: pick_bin(&["/usr/bin/pvesh", "/usr/sbin/pvesh", "pvesh"])
                .ok_or("pvesh not found - is this a Proxmox host?")?,
            nix: pick_bin(&["/nix/var/nix/profiles/default/bin/nix", "nix"])
                .ok_or("nix not found")?,
            nix_store: pick_bin(&["/nix/var/nix/profiles/default/bin/nix-store", "nix-store"])
                .ok_or("nix-store not found")?,
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

fn inspect_storage(bins: &Bins, storage: &str) -> Result<StorageInfo, String> {
    let storage_api_path = format!("/storage/{storage}");
    let output = command_output(Command::new(&bins.pvesh).args([
        "get",
        &storage_api_path,
        "--output-format",
        "json",
    ]))
    .map_err(|err| format!("failed to inspect Proxmox storage {storage}: {err}"))?;
    parse_storage_info_json(&output)
        .map_err(|err| format!("failed to parse Proxmox storage {storage}: {err}"))
}

fn parse_storage_info_json(output: &str) -> Result<StorageInfo, String> {
    let value: serde_json::Value =
        serde_json::from_str(output).map_err(|err| format!("invalid storage JSON: {err}"))?;
    Ok(StorageInfo {
        shared: storage_json_bool(value.get("shared")),
        path: value
            .get("path")
            .and_then(serde_json::Value::as_str)
            .map(PathBuf::from),
    })
}

fn storage_json_bool(value: Option<&serde_json::Value>) -> bool {
    match value {
        Some(serde_json::Value::Bool(value)) => *value,
        Some(serde_json::Value::Number(value)) => value.as_i64() == Some(1),
        Some(serde_json::Value::String(value)) => {
            matches!(value.as_str(), "1" | "yes" | "true")
        }
        _ => false,
    }
}

fn take_shared_template_lock(
    storage: &StorageInfo,
    storage_name: &str,
    template_name: &str,
) -> Result<Flock<File>, String> {
    let storage_path = storage.path.as_ref().ok_or_else(|| {
        format!(
            "template storage {storage_name} is shared but has no filesystem path in pvesh storage API; cannot coordinate cluster-safe template creation"
        )
    })?;
    let lock_dir = storage_path.join(".proxnix-locks");
    fs::create_dir_all(&lock_dir).map_err(|err| {
        format!(
            "failed to create shared template lock dir {}: {err}",
            lock_dir.display()
        )
    })?;
    let lock_path = lock_dir.join(format!(
        "template-bootstrap-{}-{}.lock",
        sanitize_lock_name(storage_name),
        sanitize_lock_name(template_name)
    ));
    let file = File::create(&lock_path).map_err(|err| {
        format!(
            "failed to open shared template lock {}: {err}",
            lock_path.display()
        )
    })?;
    Flock::lock(file, FlockArg::LockExclusive).map_err(|err| {
        format!(
            "failed to lock shared template creation at {}: {err:?}",
            lock_path.display(),
        )
    })
}

fn sanitize_lock_name(value: &str) -> String {
    value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '_') {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

fn template_exists(bins: &Bins, storage: &str, template_name: &str) -> Result<bool, String> {
    let output = command_output(Command::new(&bins.pveam).args(["list", storage]))
        .map_err(|err| format!("failed to list templates on storage {storage}: {err}"))?;
    Ok(parse_template_exists(&output, storage, template_name))
}

fn parse_template_exists(output: &str, storage: &str, template_name: &str) -> bool {
    let expected_volid = format!("{storage}:vztmpl/{template_name}");
    output
        .lines()
        .skip(1)
        .filter_map(|line| line.split_whitespace().next())
        .any(|candidate| {
            candidate == template_name
                || candidate == expected_volid
                || candidate.ends_with(&format!("/{}", template_name))
        })
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct BootstrapPlan {
    source_template: String,
    rootfs_storage: String,
    temp_vmid: String,
}

impl BootstrapPlan {
    fn resolve(bins: &Bins, options: &Options) -> Result<Self, String> {
        Ok(Self {
            source_template: resolve_source_template(bins, options)?,
            rootfs_storage: resolve_rootfs_storage(bins, options)?,
            temp_vmid: resolve_temp_vmid(bins, options)?,
        })
    }
}

fn resolve_source_template(bins: &Bins, options: &Options) -> Result<String, String> {
    if !options.source_template_name.is_empty() {
        let volid =
            normalize_template_volid(&options.template_storage, &options.source_template_name);
        verify_template_volid(bins, &volid)?;
        return Ok(volid);
    }
    detect_latest_nixos_template(bins, &options.template_storage)
}

fn normalize_template_volid(storage: &str, template_name: &str) -> String {
    if template_name.contains(':') {
        template_name.to_owned()
    } else {
        format!("{storage}:vztmpl/{template_name}")
    }
}

fn verify_template_volid(bins: &Bins, volid: &str) -> Result<(), String> {
    command_output(Command::new(&bins.pvesm).args(["path", volid]))
        .map(|_| ())
        .map_err(|err| format!("template {volid} is not available: {err}"))
}

fn detect_latest_nixos_template(bins: &Bins, storage: &str) -> Result<String, String> {
    let output = command_output(Command::new(&bins.pveam).args(["list", storage]))
        .map_err(|err| format!("failed to list templates on storage {storage}: {err}"))?;
    output
        .lines()
        .skip(1)
        .filter_map(|line| line.split_whitespace().next())
        .filter(|candidate| {
            let lower = candidate.to_ascii_lowercase();
            lower.contains("nixos") && lower.contains(".tar.")
        })
        .map(|candidate| normalize_template_volid(storage, candidate))
        .max()
        .ok_or_else(|| {
            format!(
                "could not find a NixOS LXC template on storage {storage}; pass --source-template-name"
            )
        })
}

fn resolve_rootfs_storage(bins: &Bins, options: &Options) -> Result<String, String> {
    if !matches!(options.rootfs_storage.as_str(), "" | "auto" | "latest") {
        return Ok(options.rootfs_storage.clone());
    }
    detect_rootfs_storage(bins)
}

fn detect_rootfs_storage(bins: &Bins) -> Result<String, String> {
    let output = command_output(Command::new(&bins.pvesm).args(["status", "--content", "rootdir"]))
        .map_err(|err| format!("failed to inspect rootfs storage: {err}"))?;
    let storages = output
        .lines()
        .skip(1)
        .filter_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            (fields.len() > 2 && fields[2] == "active").then(|| fields[0].to_owned())
        })
        .collect::<Vec<_>>();

    for preferred in ["local-zfs", "local-lvm", "local"] {
        if storages.iter().any(|storage| storage == preferred) {
            return Ok(preferred.to_owned());
        }
    }
    storages.into_iter().next().ok_or_else(|| {
        "could not auto-detect active rootdir-capable storage; pass --rootfs-storage".to_owned()
    })
}

fn resolve_temp_vmid(bins: &Bins, options: &Options) -> Result<String, String> {
    if !options.temp_vmid.is_empty() {
        if !options.temp_vmid.bytes().all(|byte| byte.is_ascii_digit()) {
            return Err("--temp-vmid must be numeric".to_owned());
        }
        return Ok(options.temp_vmid.clone());
    }
    let output = command_output(Command::new(&bins.pvesh).args(["get", "/cluster/nextid"]))
        .map_err(|err| format!("failed to allocate temporary VMID: {err}"))?;
    let vmid = output.trim().to_owned();
    if vmid.bytes().all(|byte| byte.is_ascii_digit()) {
        Ok(vmid)
    } else {
        Err(format!("pvesh returned invalid temporary VMID: {vmid}"))
    }
}

fn run_bootstrap(bins: &Bins, options: &Options, plan: &BootstrapPlan) -> Result<(), String> {
    println!(
        "seeding host store from {} using temporary VMID {}",
        plan.source_template, plan.temp_vmid
    );
    let mut temp = TemporaryContainer::create(bins, plan)?;
    let rootfs = temp.mount(bins)?;
    let system_path = rootfs_system_path(&rootfs)?;
    load_rootfs_registration(bins, &rootfs)?;
    nix_copy_from_rootfs(bins, &rootfs, &system_path)?;
    println!("seeded host store with template system {system_path}");
    reconcile_phase::build_golden_main(&[])
        .map_err(|err| format!("failed to build proxnix golden closure: {err}"))?;
    if options.keep_temp {
        temp.keep();
        println!(
            "kept temporary CT {} mounted at {}",
            plan.temp_vmid,
            rootfs.display()
        );
    } else {
        temp.cleanup(bins)?;
    }
    println!(
        "proxnix golden closure is built; template archive creation for {} is still a separate step",
        options.template_name
    );
    Ok(())
}

#[derive(Debug)]
struct TemporaryContainer {
    vmid: String,
    mounted: bool,
    keep: bool,
}

impl TemporaryContainer {
    fn create(bins: &Bins, plan: &BootstrapPlan) -> Result<Self, String> {
        run_status(
            Command::new(&bins.pct)
                .arg("create")
                .arg(&plan.temp_vmid)
                .arg(&plan.source_template)
                .arg("--ostype")
                .arg("nixos")
                .arg("--hostname")
                .arg(format!("proxnix-template-seed-{}", plan.temp_vmid))
                .arg("--rootfs")
                .arg(format!("{}:8", plan.rootfs_storage))
                .arg("--memory")
                .arg("512")
                .arg("--swap")
                .arg("0")
                .arg("--cores")
                .arg("1")
                .arg("--unprivileged")
                .arg("1")
                .arg("--features")
                .arg("nesting=1,keyctl=1"),
            &format!("failed to create temporary CT {}", plan.temp_vmid),
        )?;
        Ok(Self {
            vmid: plan.temp_vmid.clone(),
            mounted: false,
            keep: false,
        })
    }

    fn mount(&mut self, bins: &Bins) -> Result<PathBuf, String> {
        let output = command_output(Command::new(&bins.pct).args(["mount", &self.vmid]))
            .map_err(|err| format!("failed to mount temporary CT {}: {err}", self.vmid))?;
        self.mounted = true;
        parse_pct_mount_rootfs(&output)
            .unwrap_or_else(|| PathBuf::from(format!("/var/lib/lxc/{}/rootfs", self.vmid)))
            .canonicalize()
            .map_err(|err| {
                format!(
                    "failed to resolve mounted rootfs for CT {}: {err}",
                    self.vmid
                )
            })
    }

    fn keep(&mut self) {
        self.keep = true;
    }

    fn cleanup(&mut self, bins: &Bins) -> Result<(), String> {
        if self.mounted {
            run_status(
                Command::new(&bins.pct).args(["unmount", &self.vmid]),
                &format!("failed to unmount temporary CT {}", self.vmid),
            )?;
            self.mounted = false;
        }
        run_status(
            Command::new(&bins.pct).args(["destroy", &self.vmid]),
            &format!("failed to destroy temporary CT {}", self.vmid),
        )?;
        self.keep = true;
        Ok(())
    }
}

impl Drop for TemporaryContainer {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let Some(pct) = pick_bin(&["/usr/sbin/pct", "/usr/bin/pct", "pct"]) else {
            return;
        };
        if self.mounted {
            let _ = Command::new(&pct)
                .args(["unmount", &self.vmid])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = Command::new(&pct)
            .args(["destroy", &self.vmid])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

fn parse_pct_mount_rootfs(output: &str) -> Option<PathBuf> {
    let quote_start = output.find('\'')?;
    let rest = &output[quote_start + 1..];
    let quote_end = rest.find('\'')?;
    Some(PathBuf::from(&rest[..quote_end]))
}

fn rootfs_system_path(rootfs: &Path) -> Result<String, String> {
    resolve_rootfs_profile(rootfs, Path::new("/nix/var/nix/profiles/system"))
        .or_else(|| rootfs_registration_system(rootfs))
        .or_else(|| rootfs_store_system(rootfs))
        .ok_or_else(|| {
            format!(
                "{} has no NixOS system profile or registered nixos-system store path",
                rootfs.display()
            )
        })
}

fn resolve_rootfs_profile(rootfs: &Path, profile: &Path) -> Option<String> {
    let mut current = profile.to_path_buf();
    for _ in 0..8 {
        let host_path = rootfs_path(rootfs, &current);
        let target = fs::read_link(host_path).ok()?;
        if target.starts_with("/nix/store") {
            return Some(target.display().to_string());
        }
        current = if target.is_absolute() {
            target
        } else {
            current.parent()?.join(target)
        };
    }
    None
}

fn rootfs_path(rootfs: &Path, absolute_path: &Path) -> PathBuf {
    rootfs.join(absolute_path.strip_prefix("/").unwrap_or(absolute_path))
}

fn rootfs_registration_system(rootfs: &Path) -> Option<String> {
    fs::read_to_string(rootfs.join("nix-path-registration"))
        .ok()?
        .lines()
        .find(|line| is_nixos_system_store_path(line))
        .map(str::to_owned)
}

fn rootfs_store_system(rootfs: &Path) -> Option<String> {
    fs::read_dir(rootfs.join("nix/store"))
        .ok()?
        .filter_map(Result::ok)
        .filter_map(|entry| entry.file_name().into_string().ok())
        .map(|name| format!("/nix/store/{name}"))
        .find(|path| is_nixos_system_store_path(path))
}

fn is_nixos_system_store_path(path: &str) -> bool {
    path.starts_with("/nix/store/") && path.contains("-nixos-system-")
}

fn nix_copy_from_rootfs(bins: &Bins, rootfs: &Path, system_path: &str) -> Result<(), String> {
    run_status(
        Command::new(&bins.nix)
            .arg("copy")
            .arg("--no-check-sigs")
            .arg("--option")
            .arg("substituters")
            .arg("")
            .arg("--from")
            .arg(format!("local?root={}", rootfs.display()))
            .arg(system_path),
        "failed to copy NixOS template closure into host store",
    )
}

fn load_rootfs_registration(bins: &Bins, rootfs: &Path) -> Result<(), String> {
    let registration = rootfs.join("nix-path-registration");
    if !registration.is_file() {
        return Ok(());
    }
    let input = File::open(&registration).map_err(|err| {
        format!(
            "failed to open template Nix registration {}: {err}",
            registration.display()
        )
    })?;
    run_status(
        Command::new(&bins.nix_store)
            .arg("--store")
            .arg(format!("local?root={}", rootfs.display()))
            .arg("--load-db")
            .stdin(Stdio::from(input)),
        "failed to load template Nix registration",
    )
}

fn run_status(command: &mut Command, context: &str) -> Result<(), String> {
    let output = command
        .output()
        .map_err(|err| format!("{context}: {err}"))?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        Err(context.to_owned())
    } else {
        Err(format!("{context}: {stderr}"))
    }
}

fn decide_template_action(shared: bool, exists: bool, force: bool) -> TemplateAction {
    if shared && exists && !force {
        TemplateAction::ReuseSharedExisting
    } else {
        TemplateAction::CreateOrRefresh
    }
}

fn command_output(command: &mut Command) -> std::io::Result<String> {
    let output = command.stderr(Stdio::null()).output()?;
    if !output.status.success() {
        return Err(std::io::Error::other("command failed"));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_storage_info_from_pvesh_json() {
        assert_eq!(
            parse_storage_info_json(
                r#"{"storage":"mooseFS","type":"dir","path":"/mnt/moosefs/pve","shared":1}"#
            )
            .unwrap(),
            StorageInfo {
                shared: true,
                path: Some(PathBuf::from("/mnt/moosefs/pve")),
            }
        );
        assert_eq!(
            parse_storage_info_json(r#"{"storage":"local","type":"dir","shared":0}"#).unwrap(),
            StorageInfo {
                shared: false,
                path: None,
            }
        );
    }

    #[test]
    fn detects_existing_template_from_pveam_list() {
        let output = "\
NAME                                                     SIZE
local:vztmpl/nixos.tar.xz                               120M
mooseFS:vztmpl/proxnix-nixos-golden.tar.xz              900M
";
        assert!(parse_template_exists(
            output,
            "mooseFS",
            "proxnix-nixos-golden.tar.xz"
        ));
        assert!(!parse_template_exists(
            output,
            "mooseFS",
            "proxnix-nixos-golden-new.tar.xz"
        ));
    }

    #[test]
    fn skips_existing_template_only_on_shared_storage() {
        assert_eq!(
            decide_template_action(true, true, false),
            TemplateAction::ReuseSharedExisting
        );
        assert_eq!(
            decide_template_action(false, true, false),
            TemplateAction::CreateOrRefresh
        );
        assert_eq!(
            decide_template_action(true, false, false),
            TemplateAction::CreateOrRefresh
        );
        assert_eq!(
            decide_template_action(true, true, true),
            TemplateAction::CreateOrRefresh
        );
    }
}
