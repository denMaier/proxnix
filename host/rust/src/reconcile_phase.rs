use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use super::{default_node_name, env_path, find_in_path, render_authority, take_arg};

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

pub(crate) fn build_golden_main(args: &[String]) -> Result<(), String> {
    let options = parse_build_golden_args(args)?;
    let config = BuildGoldenConfig::from_env();
    build_golden(&config, &options).map_err(|err| format!("build-golden failed: {err}"))
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
}
