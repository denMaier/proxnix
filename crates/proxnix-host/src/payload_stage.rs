use std::env;
use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use unix::fcntl::{AtFlags, AT_FDCWD};
use unix::unistd::{fchownat, Uid};

use crate::common::{env_path, remove_path_if_exists, set_mode, valid_vmid, DEFAULT_PROXNIX_DIR};
use crate::pve_conf::{generate_proxmox_nix, parse_pve_conf, parse_pve_conf_raw_content};
use crate::secret_bundle::{decrypt_age_text, BUNDLE_FILE};

#[derive(Debug, Clone)]
struct StagePaths {
    proxnix_dir: PathBuf,
    proxnix_priv_dir: PathBuf,
    authority_dir: PathBuf,
    host_state_dir: PathBuf,
    run_dir: PathBuf,
    secrets_guest_bin: PathBuf,
}

#[derive(Debug, Clone)]
pub(crate) struct StagedPayload {
    pub(crate) stage_dir: PathBuf,
    pub(crate) bind_config_dir: PathBuf,
    pub(crate) bind_runtime_dir: PathBuf,
    pub(crate) bind_secrets_dir: PathBuf,
    pub(crate) copy_runtime_bin_dir: PathBuf,
}

impl StagedPayload {
    fn from_stage_dir(stage_dir: PathBuf) -> Self {
        Self {
            bind_config_dir: stage_dir.join("bind/config"),
            bind_runtime_dir: stage_dir.join("bind/runtime"),
            bind_secrets_dir: stage_dir.join("bind/secrets"),
            copy_runtime_bin_dir: stage_dir.join("copy/runtime/bin"),
            stage_dir,
        }
    }
}

impl StagePaths {
    fn from_env() -> Self {
        let proxnix_dir = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
        let proxnix_priv_dir = env::var_os("PROXNIX_PRIV_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| proxnix_dir.join("private"));
        let authority_dir = env::var_os("PROXNIX_AUTHORITY_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| proxnix_dir.join("authority"));
        let host_state_dir = env_path("PROXNIX_HOST_STATE_DIR", "/etc/proxnix");
        let run_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
        let lib_dir = env_path("PROXNIX_LIB_DIR", "/usr/local/lib/proxnix");
        let secrets_guest_bin = env::var_os("PROXNIX_SECRETS_GUEST_BIN")
            .map(PathBuf::from)
            .unwrap_or_else(|| lib_dir.join("proxnix-secrets-guest"));

        Self {
            proxnix_dir,
            proxnix_priv_dir,
            authority_dir,
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

    fn rendered_container_dir(&self, vmid: &str) -> PathBuf {
        self.authority_dir.join("containers").join(vmid)
    }

    fn rendered_site_nix(&self) -> PathBuf {
        self.authority_dir.join("site.nix")
    }

    fn prepare_stage_base(&self) -> io::Result<()> {
        fs::create_dir_all(&self.run_dir)?;
        set_mode(&self.run_dir, 0o711)
    }

    fn stage_dir(&self, vmid: &str) -> PathBuf {
        self.run_dir.join(vmid)
    }
}

pub(crate) fn stage_payload_for_reconcile(vmid: &str) -> Result<StagedPayload, String> {
    let paths = StagePaths::from_env();
    paths
        .prepare_stage_base()
        .map_err(|err| format!("failed to prepare stage base: {err}"))?;
    let stage_dir = paths.stage_dir(vmid);
    let mut stage_complete = false;
    let result = stage_payload_run(&paths, vmid, None, &stage_dir, &mut stage_complete);
    if result.is_err() && !stage_complete {
        let _ = remove_path_if_exists(&stage_dir);
    }
    result.map(|()| StagedPayload::from_stage_dir(stage_dir))
}

fn stage_payload_run(
    paths: &StagePaths,
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
    let rendered_container_dir = paths.rendered_container_dir(vmid);
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

    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        &format!(
            "Starting payload stage (PVE conf: {}, stage: {}).",
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
            stage_log(
                "proxnix-stage",
                "stage",
                vmid,
                &format!("ERROR: missing required shared file: {}", source.display()),
            );
            return Err("required shared file missing; rerun host install".to_owned());
        }
    }

    if !paths.secrets_guest_bin.is_file() {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            &format!("ERROR: {} not found.", paths.secrets_guest_bin.display()),
        );
        return Err("proxnix-secrets-guest missing; rerun host install".to_owned());
    }

    stage_log(
        "proxnix-stage",
        "stage",
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
    let site_nix = if paths.rendered_site_nix().is_file() {
        paths.rendered_site_nix()
    } else {
        paths.proxnix_dir.join("site.nix")
    };
    if site_nix.is_file() {
        copy_file(&site_nix, &managed_dir.join("site.nix"), 0o600)?;
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Included optional site.nix.",
        );
    } else {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "No optional site.nix present.",
        );
    }

    for legacy_yaml in [
        container_dir.join("proxmox.yaml"),
        container_dir.join("user.yaml"),
    ] {
        if legacy_yaml.is_file() {
            stage_log(
                "proxnix-stage",
                "stage",
                vmid,
                &format!(
                    "ERROR: legacy YAML config is no longer supported: {}",
                    legacy_yaml.display()
                ),
            );
            return Err("legacy YAML config is no longer supported".to_owned());
        }
    }

    let rendered_proxmox_nix = rendered_container_dir.join("proxmox.nix");
    if rendered_proxmox_nix.is_file() {
        copy_file(
            &rendered_proxmox_nix,
            &managed_dir.join("proxmox.nix"),
            0o600,
        )?;
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Staged host-rendered Proxmox CT config.",
        );
    } else {
        let rendered = generate_proxmox_nix(
            &parse_pve_conf(&pve_conf)
                .map_err(|err| format!("failed to read {}: {err}", pve_conf.display()))?,
        );
        write_text_file(&managed_dir.join("proxmox.nix"), &rendered, 0o600)
            .map_err(|err| format!("failed to render Proxmox CT config: {err}"))?;
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Rendered Proxmox CT config.",
        );
    }

    if dir_has_entries(&container_dir.join("quadlets"))? {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            &format!(
                "ERROR: raw quadlet passthrough is no longer supported: {}",
                container_dir.join("quadlets").display()
            ),
        );
        return Err("raw quadlet passthrough is no longer supported".to_owned());
    }

    let rendered_template_dir = rendered_container_dir.join("_template");
    if rendered_template_dir.is_dir() {
        stage_rendered_templates(vmid, &rendered_template_dir, &managed_template_dir)?;
    } else {
        stage_templates(
            vmid,
            &template_selector_dir,
            &container_template_dir,
            &managed_template_dir,
        )?;
    }

    let rendered_dropin_dir = rendered_container_dir.join("dropins");
    let rendered_runtime_bin_dir = rendered_container_dir.join("runtime-bin");
    if rendered_dropin_dir.is_dir() || rendered_runtime_bin_dir.is_dir() {
        stage_rendered_dropins(
            vmid,
            &rendered_dropin_dir,
            &rendered_runtime_bin_dir,
            &managed_dropin_dir,
            &copy_runtime_bin_dir,
        )?;
    } else {
        stage_dropins(
            vmid,
            &dropin_dir,
            &managed_dropin_dir,
            &copy_runtime_bin_dir,
        )?;
    }

    let desired_config_hash = hash_tree(&bind_config_dir)
        .map_err(|err| format!("failed to hash staged config tree: {err}"))?;
    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        &format!("Computed desired config hash: {desired_config_hash}."),
    );

    let effective_secrets = container_priv_dir.join(BUNDLE_FILE);
    if effective_secrets.is_file() {
        copy_file(
            &effective_secrets,
            &secrets_stage_dir.join(BUNDLE_FILE),
            0o600,
        )?;
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Staged effective encrypted secret bundle.",
        );
    } else {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "No effective encrypted secret bundle found.",
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

    let identity_store = container_priv_dir.join("age_identity.age");
    if identity_store.is_file() {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Decrypting host-relay container identity for reconcile staging.",
        );
        decrypt_host_identity_store_to_file(
            &identity_store,
            &secrets_stage_dir.join("identity"),
            &paths.host_state_dir.join("host_relay_identity"),
        )
        .map_err(|err| format!("failed to decrypt host-relay container identity: {err}"))?;
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "Staged decrypted container identity.",
        );
    } else {
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            "No host-relay container identity store found.",
        );
    }

    let host_subuid = determine_host_root_uid(&pve_conf)
        .map_err(|err| format!("could not determine container host root UID: {err}"))?;
    restrict_stage_tree(vmid, stage_dir, &copy_runtime_bin_dir, &host_subuid)?;

    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        &format!(
            "Rendered staged state at {} (host root uid: {host_subuid})",
            stage_dir.display()
        ),
    );
    *stage_complete = true;
    Ok(())
}

fn stage_log(logger_tag: &str, prefix: &str, vmid: &str, message: &str) {
    let line = format!(
        "[proxnix-{prefix}][{}] {message}",
        if vmid.is_empty() { "unknown" } else { vmid }
    );
    eprintln!("{line}");
    let formatter = syslog::Formatter3164 {
        facility: syslog::Facility::LOG_USER,
        hostname: None,
        process: logger_tag.to_owned(),
        pid: std::process::id(),
    };
    let _ = syslog::unix(formatter).and_then(|mut writer| writer.info(line));
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

fn stage_rendered_templates(
    vmid: &str,
    rendered_template_dir: &Path,
    managed_template_dir: &Path,
) -> Result<(), String> {
    remove_path_if_exists(managed_template_dir)
        .map_err(|err| format!("failed to replace rendered templates: {err}"))?;
    copy_dir_recursive(rendered_template_dir, managed_template_dir)?;
    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        "Staged host-rendered selected templates.",
    );
    Ok(())
}

fn stage_templates(
    vmid: &str,
    selector_dir: &Path,
    template_dir: &Path,
    managed_template_dir: &Path,
) -> Result<(), String> {
    if !selector_dir.is_dir() {
        stage_log(
            "proxnix-stage",
            "stage",
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
        stage_log(
            "proxnix-stage",
            "stage",
            vmid,
            &format!("Included template: {template_name}"),
        );
        count += 1;
    }
    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        &format!("Processed {count} selected template(s)."),
    );
    Ok(())
}

fn stage_rendered_dropins(
    vmid: &str,
    rendered_dropin_dir: &Path,
    rendered_runtime_bin_dir: &Path,
    managed_dropin_dir: &Path,
    copy_runtime_bin_dir: &Path,
) -> Result<(), String> {
    let mut nix_dropins = 0;
    let mut runtime_files = 0;
    let mut dir_dropins = 0;

    if rendered_dropin_dir.is_dir() {
        for entry in sorted_dir_entries(rendered_dropin_dir)? {
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
                    Some("container" | "volume" | "network" | "pod" | "image" | "build") => {
                        return Err(format!(
                            "raw Quadlet drop-ins are no longer supported: {}",
                            path.display()
                        ));
                    }
                    _ => {
                        stage_log(
                            "proxnix-stage",
                            "stage",
                            vmid,
                            &format!("Ignored unknown rendered drop-in type: {name}"),
                        );
                    }
                }
            } else if path.is_dir() {
                let dest = managed_dropin_dir.join(&name);
                remove_path_if_exists(&dest)
                    .map_err(|err| format!("failed to replace rendered drop-in dir: {err}"))?;
                copy_dir_recursive(&path, &dest)?;
                dir_dropins += 1;
            }
        }
    }

    if rendered_runtime_bin_dir.is_dir() {
        for entry in sorted_dir_entries(rendered_runtime_bin_dir)? {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if path.is_file() {
                copy_file(&path, &copy_runtime_bin_dir.join(&name), 0o700)?;
                runtime_files += 1;
            }
        }
    }

    stage_log(
        "proxnix-stage",
        "stage",
        vmid,
        &format!(
            "Processed host-rendered drop-ins: {nix_dropins} nix file(s), {runtime_files} runtime file(s), {dir_dropins} directory/directories."
        ),
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
        stage_log(
            "proxnix-stage",
            "stage",
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
                    stage_log(
                        "proxnix-stage",
                        "stage",
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
    stage_log(
        "proxnix-stage",
        "stage",
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
    Ok(hex_digest(&Sha256::digest(fs::read(path)?)))
}

fn sha256sum_bytes(bytes: &[u8]) -> io::Result<String> {
    Ok(hex_digest(&Sha256::digest(bytes)))
}

fn hex_digest(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(64);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
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
    let ciphertext = fs::read_to_string(store)?;
    let relay_identity = fs::read_to_string(relay_key)?;
    let rendered = decrypt_age_text(&ciphertext, &relay_identity).map_err(io::Error::other)?;
    write_text_file(out, &rendered, 0o600)
}

pub(crate) fn parse_identity_payload(content: &str) -> io::Result<String> {
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

pub(crate) fn determine_host_root_uid(pve_conf: &Path) -> io::Result<String> {
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
    stage_log(
        "proxnix-stage",
        "stage",
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
    let uid = host_subuid
        .parse::<u32>()
        .map_err(|err| format!("invalid host root UID {host_subuid}: {err}"))?;
    chown_tree(stage_dir, Uid::from_raw(uid))?;
    chmod_tree(stage_dir, 0o700, 0o400)?;
    chmod_files(copy_runtime_bin_dir, 0o500)?;
    Ok(())
}

fn chown_tree(root: &Path, uid: Uid) -> Result<(), String> {
    if !root.exists() {
        return Ok(());
    }
    let metadata = fs::symlink_metadata(root)
        .map_err(|err| format!("failed to stat {}: {err}", root.display()))?;
    fchownat(
        AT_FDCWD,
        root,
        Some(uid),
        None,
        AtFlags::AT_SYMLINK_NOFOLLOW,
    )
    .map_err(|err| format!("failed to chown {}: {err}", root.display()))?;
    if metadata.is_dir() {
        for entry in sorted_dir_entries(root)? {
            chown_tree(&entry.path(), uid)?;
        }
    }
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
