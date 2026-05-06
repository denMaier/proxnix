use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::common::{
    env_path, find_in_path, remove_path_if_exists, valid_vmid, HostResult, DEFAULT_PROXNIX_DIR,
};

#[derive(Debug, PartialEq, Eq)]
struct Options {
    dry_run: bool,
    prune_host_profile: bool,
    collect_store: bool,
    host_profile: PathBuf,
    profile_generations: String,
}

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let options = parse_args(args)?;
    let proxnix_dir = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
    let run_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
    let gcroot_dir = env::var_os("PROXNIX_GCROOT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| proxnix_dir.join("gcroots/deploy"));

    run(&run_dir, &gcroot_dir, &options).map_err(|err| format!("gc command failed: {err}"))?;
    Ok(())
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut options = Options::default();
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--dry-run" => options.dry_run = true,
            "--no-profile-prune" => options.prune_host_profile = false,
            "--no-store-gc" => options.collect_store = false,
            "--host-profile" => {
                index += 1;
                options.host_profile = args
                    .get(index)
                    .map(PathBuf::from)
                    .ok_or("--host-profile requires a value")?;
            }
            "--profile-generations" => {
                index += 1;
                options.profile_generations = args
                    .get(index)
                    .cloned()
                    .ok_or("--profile-generations requires a value")?;
            }
            "-h" | "--help" => {
                usage();
                return Ok(options);
            }
            other => return Err(format!("unknown gc argument: {other}")),
        }
        index += 1;
    }
    Ok(options)
}

impl Default for Options {
    fn default() -> Self {
        Self {
            dry_run: false,
            prune_host_profile: true,
            collect_store: true,
            host_profile: PathBuf::from("/nix/var/nix/profiles/proxnix-host"),
            profile_generations: "old".to_owned(),
        }
    }
}

fn usage() {
    eprintln!(
        "\
Usage:
  proxnix-host gc [options]

Options:
  --dry-run                       Print planned cleanup without deleting
  --no-profile-prune              Keep old proxnix-host profile generations
  --no-store-gc                   Do not run nix-store --gc
  --host-profile PATH             Host tool profile to prune
                                  (default: /nix/var/nix/profiles/proxnix-host)
  --profile-generations SPEC      Generation spec passed to nix-env
                                  --delete-generations (default: old)

Removes copied/staged pre-start config directories from /run/proxnix when the
CT is not running, preserves the golden-template GC root, and removes stale
per-CT desired closure GC roots. It also prunes old proxnix-host profile
generations and runs a controlled Nix store GC after proxnix roots are clean.
"
    );
}

fn run(run_dir: &Path, gcroot_dir: &Path, options: &Options) -> io::Result<()> {
    prune_stage_dirs(run_dir, options.dry_run)?;
    prune_gc_roots(gcroot_dir, options.dry_run)?;
    if options.prune_host_profile {
        prune_host_profile_generations(options)?;
    }
    if options.collect_store {
        collect_nix_store(options.dry_run)?;
    }
    Ok(())
}

fn prune_stage_dirs(run_dir: &Path, dry_run: bool) -> io::Result<()> {
    if !run_dir.is_dir() {
        return Ok(());
    }
    for entry in sorted_dir_entries(run_dir)? {
        let dir = entry.path();
        if !dir.is_dir() {
            continue;
        }
        let vmid = entry.file_name().to_string_lossy().into_owned();
        if ct_is_running(&vmid) {
            log(&format!(
                "released stage dir for booted CT {vmid} (content already copied into guest)"
            ));
            remove_path(&dir, dry_run)?;
        } else {
            remove_path(&dir, dry_run)?;
            log(&format!("removed stage dir for CT {vmid}"));
        }
    }
    Ok(())
}

fn prune_gc_roots(gcroot_dir: &Path, dry_run: bool) -> io::Result<()> {
    if !gcroot_dir.is_dir() {
        return Ok(());
    }
    for entry in sorted_dir_entries(gcroot_dir)? {
        let path = entry.path();
        if !path.exists() && fs::symlink_metadata(&path).is_err() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if name == "golden-template" {
            continue;
        }
        let Some(vmid) = name.strip_suffix("-desired") else {
            continue;
        };
        if !valid_vmid(vmid) || ct_exists_locally(vmid) {
            continue;
        }
        remove_path(&path, dry_run)?;
        log(&format!(
            "removed stale desired closure GC root for CT {vmid}"
        ));
    }
    Ok(())
}

fn remove_path(path: &Path, dry_run: bool) -> io::Result<()> {
    if dry_run {
        log(&format!("would remove {}", path.display()));
        Ok(())
    } else {
        remove_path_if_exists(path)
    }
}

fn prune_host_profile_generations(options: &Options) -> io::Result<()> {
    if !path_exists(&options.host_profile) {
        log(&format!(
            "host profile {} does not exist; skipped profile generation pruning",
            options.host_profile.display()
        ));
        return Ok(());
    }
    if options.dry_run {
        log(&format!(
            "would prune host profile {} generations matching {}",
            options.host_profile.display(),
            options.profile_generations
        ));
        return Ok(());
    }
    let nix_env = find_nix_bin("nix-env")?;
    run_command(
        Command::new(nix_env)
            .arg("--profile")
            .arg(&options.host_profile)
            .arg("--delete-generations")
            .arg(&options.profile_generations),
        "pruned old proxnix-host profile generations",
    )
}

fn collect_nix_store(dry_run: bool) -> io::Result<()> {
    if dry_run {
        log("would run nix-store --gc");
        return Ok(());
    }
    let nix_store = find_nix_bin("nix-store")?;
    run_command(
        Command::new(nix_store).arg("--gc"),
        "collected unreachable Nix store paths",
    )
}

fn run_command(command: &mut Command, success_message: &str) -> io::Result<()> {
    let output = command.output()?;
    if output.status.success() {
        log(success_message);
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        Err(io::Error::other(success_message.to_owned()))
    } else {
        Err(io::Error::other(stderr))
    }
}

fn find_nix_bin(name: &str) -> io::Result<PathBuf> {
    if let Some(path) = find_in_path(name) {
        return Ok(path);
    }
    for candidate in [
        format!("/nix/var/nix/profiles/default/bin/{name}"),
        format!("/run/current-system/sw/bin/{name}"),
    ] {
        if let Some(path) = find_in_path(&candidate) {
            return Ok(path);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!("{name} not found"),
    ))
}

fn path_exists(path: &Path) -> bool {
    path.exists() || fs::symlink_metadata(path).is_ok()
}

fn log(message: &str) {
    eprintln!("proxnix-host gc: {message}");
}

fn ct_exists_locally(vmid: &str) -> bool {
    pct_status(vmid).is_some()
}

fn ct_is_running(vmid: &str) -> bool {
    pct_status(vmid)
        .as_deref()
        .is_some_and(|status| status.lines().any(|line| line.contains("status: running")))
}

fn pct_status(vmid: &str) -> Option<String> {
    let pct = find_in_path("pct")?;
    let output = Command::new(pct)
        .arg("status")
        .arg(vmid)
        .stderr(Stdio::null())
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

fn sorted_dir_entries(path: &Path) -> io::Result<Vec<fs::DirEntry>> {
    let mut entries = fs::read_dir(path)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{TestTemp, ENV_LOCK};
    use std::os::unix::fs::symlink;
    use std::os::unix::fs::PermissionsExt;

    fn write_executable(path: &Path, content: &str) {
        fs::write(path, content).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn prunes_stage_dirs_and_only_stale_desired_roots() {
        let _guard = ENV_LOCK.lock().unwrap();
        let tmp = TestTemp::new();
        let run_dir = tmp.path().join("run");
        let gcroot_dir = tmp.path().join("gcroots/deploy");
        let fake_bin = tmp.path().join("bin");
        fs::create_dir_all(run_dir.join("101")).unwrap();
        fs::create_dir_all(run_dir.join("202")).unwrap();
        fs::create_dir_all(&gcroot_dir).unwrap();
        fs::create_dir_all(&fake_bin).unwrap();
        symlink("/nix/store/golden", gcroot_dir.join("golden-template")).unwrap();
        symlink("/nix/store/desired-101", gcroot_dir.join("101-desired")).unwrap();
        symlink("/nix/store/desired-202", gcroot_dir.join("202-desired")).unwrap();
        symlink("/nix/store/other", gcroot_dir.join("not-managed")).unwrap();
        write_executable(
            &fake_bin.join("pct"),
            "#!/bin/sh\nif [ \"$1\" = status ] && [ \"$2\" = 101 ]; then printf '%s\\n' 'status: running'; exit 0; fi\nif [ \"$1\" = status ] && [ \"$2\" = 202 ]; then exit 2; fi\nexit 2\n",
        );
        write_executable(
            &fake_bin.join("nix-env"),
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$PROXNIX_TEST_NIX_ENV_ARGS\"\n",
        );
        write_executable(
            &fake_bin.join("nix-store"),
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$PROXNIX_TEST_NIX_STORE_ARGS\"\n",
        );
        let profile = tmp.path().join("profiles/proxnix-host");
        fs::create_dir_all(profile.parent().unwrap()).unwrap();
        fs::write(&profile, "").unwrap();
        let nix_env_args = tmp.path().join("nix-env.args");
        let nix_store_args = tmp.path().join("nix-store.args");
        env::set_var("PROXNIX_TEST_NIX_ENV_ARGS", &nix_env_args);
        env::set_var("PROXNIX_TEST_NIX_STORE_ARGS", &nix_store_args);
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

        run(
            &run_dir,
            &gcroot_dir,
            &Options {
                host_profile: profile.clone(),
                ..Options::default()
            },
        )
        .unwrap();

        if let Some(old_path) = old_path {
            env::set_var("PATH", old_path);
        } else {
            env::remove_var("PATH");
        }
        env::remove_var("PROXNIX_TEST_NIX_ENV_ARGS");
        env::remove_var("PROXNIX_TEST_NIX_STORE_ARGS");
        assert!(!run_dir.join("101").exists());
        assert!(!run_dir.join("202").exists());
        assert!(gcroot_dir.join("golden-template").is_symlink());
        assert!(gcroot_dir.join("101-desired").is_symlink());
        assert!(!gcroot_dir.join("202-desired").exists());
        assert!(gcroot_dir.join("not-managed").is_symlink());
        assert_eq!(
            fs::read_to_string(nix_env_args).unwrap(),
            format!("--profile {} --delete-generations old\n", profile.display())
        );
        assert_eq!(fs::read_to_string(nix_store_args).unwrap(), "--gc\n");
    }

    #[test]
    fn dry_run_does_not_remove_paths() {
        let tmp = TestTemp::new();
        let run_dir = tmp.path().join("run");
        let gcroot_dir = tmp.path().join("gcroots/deploy");
        fs::create_dir_all(run_dir.join("202")).unwrap();
        fs::create_dir_all(&gcroot_dir).unwrap();
        symlink("/nix/store/desired-202", gcroot_dir.join("202-desired")).unwrap();

        run(
            &run_dir,
            &gcroot_dir,
            &Options {
                dry_run: true,
                host_profile: tmp.path().join("profile"),
                ..Options::default()
            },
        )
        .unwrap();

        assert!(run_dir.join("202").is_dir());
        assert!(gcroot_dir.join("202-desired").is_symlink());
    }
}
