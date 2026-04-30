use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::{env_path, find_in_path, remove_path_if_exists, valid_vmid};

#[derive(Debug, PartialEq, Eq)]
struct Options {
    dry_run: bool,
}

pub(crate) fn main(args: &[String]) -> Result<(), String> {
    let options = parse_args(args)?;
    let proxnix_dir = env_path("PROXNIX_DIR", "/var/lib/proxnix");
    let run_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
    let gcroot_dir = env::var_os("PROXNIX_GCROOT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| proxnix_dir.join("gcroots/deploy"));

    run(&run_dir, &gcroot_dir, options.dry_run).map_err(|err| format!("gc command failed: {err}"))
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut dry_run = false;
    for arg in args {
        match arg.as_str() {
            "--dry-run" => dry_run = true,
            "-h" | "--help" => {
                usage();
                return Ok(Options { dry_run });
            }
            other => return Err(format!("unknown gc argument: {other}")),
        }
    }
    Ok(Options { dry_run })
}

fn usage() {
    eprintln!(
        "\
Usage:
  proxnix-host gc [--dry-run]

Removes copied/staged pre-start config directories from /run/proxnix when the
CT is not running, preserves the golden-template GC root, and removes stale
per-CT desired closure GC roots.
"
    );
}

fn run(run_dir: &Path, gcroot_dir: &Path, dry_run: bool) -> io::Result<()> {
    prune_stage_dirs(run_dir, dry_run)?;
    prune_gc_roots(gcroot_dir, dry_run)
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

fn log(message: &str) {
    eprintln!("proxnix-gc: {message}");
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

        run(&run_dir, &gcroot_dir, false).unwrap();

        if let Some(old_path) = old_path {
            env::set_var("PATH", old_path);
        } else {
            env::remove_var("PATH");
        }
        assert!(!run_dir.join("101").exists());
        assert!(!run_dir.join("202").exists());
        assert!(gcroot_dir.join("golden-template").is_symlink());
        assert!(gcroot_dir.join("101-desired").is_symlink());
        assert!(!gcroot_dir.join("202-desired").exists());
        assert!(gcroot_dir.join("not-managed").is_symlink());
    }

    #[test]
    fn dry_run_does_not_remove_paths() {
        let tmp = TestTemp::new();
        let run_dir = tmp.path().join("run");
        let gcroot_dir = tmp.path().join("gcroots/deploy");
        fs::create_dir_all(run_dir.join("202")).unwrap();
        fs::create_dir_all(&gcroot_dir).unwrap();
        symlink("/nix/store/desired-202", gcroot_dir.join("202-desired")).unwrap();

        run(&run_dir, &gcroot_dir, true).unwrap();

        assert!(run_dir.join("202").is_dir());
        assert!(gcroot_dir.join("202-desired").is_symlink());
    }
}
