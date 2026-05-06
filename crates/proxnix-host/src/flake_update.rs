use std::env;
use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{self, Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use unix::fcntl::{Flock, FlockArg};

use crate::authority::render_authority;
use crate::common::{
    default_node_name, env_path, find_in_path, set_mode, take_arg, HostResult, DEFAULT_PROXNIX_DIR,
    DEFAULT_PVE_LXC_DIR,
};

#[derive(Debug, PartialEq, Eq)]
struct Options {
    force: bool,
    frequency: String,
    inputs: Vec<String>,
}

#[derive(Debug)]
struct Config {
    root: PathBuf,
    authority: PathBuf,
    pve_lxc_dir: PathBuf,
    lock_dir: PathBuf,
    stamp_file: PathBuf,
    node_name: String,
    authority_render: Option<PathBuf>,
    now: Option<i64>,
}

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let mut inputs = env::var("PROXNIX_FLAKE_UPDATE_INPUTS")
        .unwrap_or_default()
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let options = parse_args(
        args,
        env::var("PROXNIX_FLAKE_UPDATE_FREQUENCY").unwrap_or_else(|_| "weekly".to_owned()),
        &mut inputs,
    )?;
    let config = Config::from_env()?;
    run_under_lock(&config, &options).map_err(|err| format!("flake-update failed: {err}"))?;
    Ok(())
}

impl Config {
    fn from_env() -> Result<Self, String> {
        let root = env_path("PROXNIX_DIR", DEFAULT_PROXNIX_DIR);
        let authority = env::var_os("PROXNIX_AUTHORITY_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("authority"));
        let pve_lxc_dir = env_path("PROXNIX_PVE_LXC_DIR", DEFAULT_PVE_LXC_DIR);
        let lock_dir = env_path("PROXNIX_RUN_DIR", "/run/proxnix");
        let state_dir = env::var_os("PROXNIX_STATE_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("state"));
        let stamp_file = env::var_os("PROXNIX_FLAKE_UPDATE_STAMP_FILE")
            .map(PathBuf::from)
            .unwrap_or_else(|| state_dir.join("flake-update.last-success"));
        let node_name = env::var("PROXNIX_NODE_NAME").unwrap_or_else(|_| default_node_name());
        let authority_render = env::var_os("PROXNIX_AUTHORITY_RENDER").map(PathBuf::from);
        let now = env::var("PROXNIX_FLAKE_UPDATE_NOW")
            .ok()
            .map(|value| {
                value
                    .parse::<i64>()
                    .map_err(|err| format!("invalid PROXNIX_FLAKE_UPDATE_NOW: {err}"))
            })
            .transpose()?;

        Ok(Self {
            root,
            authority,
            pve_lxc_dir,
            lock_dir,
            stamp_file,
            node_name,
            authority_render,
            now,
        })
    }
}

fn parse_args(
    args: &[String],
    mut frequency: String,
    inputs: &mut Vec<String>,
) -> Result<Options, String> {
    let mut force = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--force" => force = true,
            "--frequency" => frequency = take_arg(args, &mut index, "--frequency")?,
            "--input" => inputs.push(take_arg(args, &mut index, "--input")?),
            "--" => {
                inputs.extend(args[index + 1..].iter().cloned());
                break;
            }
            "-h" | "--help" => {
                usage();
                return Ok(Options {
                    force,
                    frequency,
                    inputs: inputs.clone(),
                });
            }
            other if other.starts_with('-') => {
                return Err(format!("unknown flake-update argument: {other}"))
            }
            other => inputs.push(other.to_owned()),
        }
        index += 1;
    }
    frequency_seconds(&frequency).map_err(|err| err.to_string())?;
    Ok(Options {
        force,
        frequency,
        inputs: inputs.clone(),
    })
}

fn usage() {
    eprintln!(
        "\
Usage:
  proxnix-host flake-update [--force] [--frequency daily|weekly|monthly|disabled] [input ...]

Updates /var/lib/proxnix/authority/flake.lock with nix flake update and copies
the resulting lock back to /var/lib/proxnix/flake.lock.
"
    );
}

fn run(config: &Config, options: &Options) -> io::Result<()> {
    if !should_update(config, options)? {
        println!(
            "proxnix-host flake-update: flake update skipped; frequency={}",
            options.frequency
        );
        return Ok(());
    }

    render(config)?;
    nix_flake_update(config, &options.inputs)?;
    persist_authority_lock(&config.root, &config.authority)?;
    write_stamp(&config.stamp_file, current_epoch(config)?)?;
    println!(
        "proxnix-host flake-update: updated flake lock; frequency={}",
        options.frequency
    );
    Ok(())
}

fn run_under_lock(config: &Config, options: &Options) -> io::Result<()> {
    let _guard = take_update_lock(&config.lock_dir)?;
    run(config, options)
}

fn take_update_lock(lock_dir: &Path) -> io::Result<Flock<File>> {
    fs::create_dir_all(lock_dir)?;
    let file = File::create(lock_dir.join("reconcile.lock"))?;
    Flock::lock(file, FlockArg::LockExclusiveNonblock).map_err(|err| {
        io::Error::other(format!(
            "another proxnix reconcile or flake update run is active: {err:?}"
        ))
    })
}

fn should_update(config: &Config, options: &Options) -> io::Result<bool> {
    if options.force {
        return Ok(true);
    }
    let Some(interval) = frequency_seconds(&options.frequency)? else {
        return Ok(false);
    };
    let now = current_epoch(config)?;
    let last = last_success_epoch(&config.stamp_file)?;
    Ok(last == 0 || now - last >= interval)
}

fn frequency_seconds(frequency: &str) -> io::Result<Option<i64>> {
    match frequency {
        "daily" => Ok(Some(86_400)),
        "weekly" => Ok(Some(604_800)),
        "monthly" => Ok(Some(2_592_000)),
        "disabled" => Ok(None),
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("invalid flake update frequency: {frequency}"),
        )),
    }
}

fn current_epoch(config: &Config) -> io::Result<i64> {
    if let Some(now) = config.now {
        return Ok(now);
    }
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(io::Error::other)?
        .as_secs() as i64)
}

fn last_success_epoch(stamp_file: &Path) -> io::Result<i64> {
    let Ok(content) = fs::read_to_string(stamp_file) else {
        return Ok(0);
    };
    Ok(content
        .lines()
        .next()
        .and_then(|line| line.trim().parse::<i64>().ok())
        .unwrap_or(0))
}

fn render(config: &Config) -> io::Result<()> {
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
        &config.node_name,
    )?;
    Ok(())
}

fn nix_flake_update(config: &Config, inputs: &[String]) -> io::Result<()> {
    let nix = find_in_path("nix")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "nix not found"))?;
    let mut command = Command::new(nix);
    command.arg("flake").arg("update");
    for input in inputs {
        command.arg(input);
    }
    command.arg("--flake").arg(&config.authority);
    let status = command.status()?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other("nix flake update failed"))
    }
}

fn persist_authority_lock(root: &Path, authority: &Path) -> io::Result<()> {
    let source = authority.join("flake.lock");
    if !source.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("nix flake update did not produce {}", source.display()),
        ));
    }
    fs::create_dir_all(root)?;
    let tmp = root.join(format!("flake.lock.{}", process::id()));
    fs::copy(&source, &tmp)?;
    set_mode(&tmp, 0o644)?;
    fs::rename(tmp, root.join("flake.lock"))
}

fn write_stamp(stamp_file: &Path, now: i64) -> io::Result<()> {
    if let Some(parent) = stamp_file.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = stamp_file.with_file_name(format!(
        "{}.{}",
        stamp_file
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("flake-update.last-success"),
        process::id()
    ));
    fs::write(&tmp, format!("{now}\n"))?;
    fs::rename(tmp, stamp_file)
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

    fn fake_nix_update() -> &'static str {
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$PROXNIX_NIX_ARGS_FILE\"\nflake=\"\"\nwhile [ $# -gt 0 ]; do\n  case \"$1\" in\n    --flake) flake=\"$2\"; shift 2;;\n    *) shift;;\n  esac\ndone\n[ -n \"$flake\" ] || exit 4\nmkdir -p \"$flake\"\nprintf '%s\\n' '{\"nodes\":{\"nixpkgs\":{\"locked\":\"new\"}}}' > \"$flake/flake.lock\"\n"
    }

    fn setup(tmp: &Path) -> (Config, PathBuf, PathBuf, PathBuf) {
        let root = tmp.join("proxnix");
        let authority = root.join("authority");
        let fake_bin = tmp.join("bin");
        let run_dir = tmp.join("run");
        let state_dir = root.join("state");
        let nix_args = tmp.join("nix-args");
        fs::create_dir_all(&fake_bin).unwrap();
        fs::create_dir_all(&root).unwrap();
        write_executable(&fake_bin.join("flock"), "#!/bin/sh\nexit 0\n");
        write_executable(&fake_bin.join("nix"), fake_nix_update());
        write_executable(
            &fake_bin.join("proxnix-authority-render"),
            fake_authority_render(),
        );

        env::set_var(
            "PATH",
            format!("{}:{}", fake_bin.display(), env::var("PATH").unwrap()),
        );
        env::set_var("PROXNIX_NIX_ARGS_FILE", &nix_args);

        (
            Config {
                root: root.clone(),
                authority: authority.clone(),
                pve_lxc_dir: tmp.join("pve/lxc"),
                lock_dir: run_dir,
                stamp_file: state_dir.join("flake-update.last-success"),
                node_name: "pve1".to_owned(),
                authority_render: Some(fake_bin.join("proxnix-authority-render")),
                now: Some(1000),
            },
            root,
            authority,
            nix_args,
        )
    }

    #[test]
    fn updates_authority_lock_and_persists_root_lock() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let old_args = env::var_os("PROXNIX_NIX_ARGS_FILE");
        let tmp = TestTemp::new();
        let (config, root, authority, nix_args) = setup(tmp.path());

        run(
            &config,
            &Options {
                force: false,
                frequency: "weekly".to_owned(),
                inputs: Vec::new(),
            },
        )
        .unwrap();

        assert_eq!(
            fs::read_to_string(authority.join("flake.lock")).unwrap(),
            "{\"nodes\":{\"nixpkgs\":{\"locked\":\"new\"}}}\n"
        );
        assert_eq!(
            fs::read_to_string(root.join("flake.lock")).unwrap(),
            "{\"nodes\":{\"nixpkgs\":{\"locked\":\"new\"}}}\n"
        );
        assert!(fs::read_to_string(nix_args)
            .unwrap()
            .contains(&format!("flake update --flake {}", authority.display())));
        assert_eq!(
            fs::read_to_string(root.join("state/flake-update.last-success")).unwrap(),
            "1000\n"
        );
        restore_env("PATH", old_path);
        restore_env("PROXNIX_NIX_ARGS_FILE", old_args);
    }

    #[test]
    fn skips_when_frequency_is_not_due() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let old_args = env::var_os("PROXNIX_NIX_ARGS_FILE");
        let tmp = TestTemp::new();
        let (config, root, _authority, nix_args) = setup(tmp.path());
        fs::create_dir_all(root.join("state")).unwrap();
        fs::write(root.join("state/flake-update.last-success"), "900\n").unwrap();

        run(
            &config,
            &Options {
                force: false,
                frequency: "weekly".to_owned(),
                inputs: Vec::new(),
            },
        )
        .unwrap();

        assert!(!nix_args.exists());
        restore_env("PATH", old_path);
        restore_env("PROXNIX_NIX_ARGS_FILE", old_args);
    }

    #[test]
    fn passes_configured_inputs_to_nix() {
        let _guard = ENV_LOCK.lock().unwrap();
        let old_path = env::var_os("PATH");
        let old_args = env::var_os("PROXNIX_NIX_ARGS_FILE");
        let tmp = TestTemp::new();
        let (config, _root, authority, nix_args) = setup(tmp.path());

        run(
            &config,
            &Options {
                force: false,
                frequency: "weekly".to_owned(),
                inputs: vec![
                    "nixpkgs".to_owned(),
                    "proxnix-extra".to_owned(),
                    "site-overrides".to_owned(),
                ],
            },
        )
        .unwrap();

        assert!(fs::read_to_string(nix_args).unwrap().contains(&format!(
            "flake update nixpkgs proxnix-extra site-overrides --flake {}",
            authority.display()
        )));
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
}
