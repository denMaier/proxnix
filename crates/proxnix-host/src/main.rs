use std::env;
use std::path::Path;

mod api;
mod authority;
mod common;
mod create_lxc;
mod ct;
mod flake_update;
mod gc;
mod payload_stage;
mod podman_secrets;
mod pve_conf;
mod reconcile_phase;
mod template_bootstrap;

use crate::common::{HostError, HostResult};

fn main() {
    if let Err(err) = run(env::args().collect()) {
        if !err.message().is_empty() {
            eprintln!("error: {err}");
        }
        std::process::exit(err.exit_code());
    }
}

fn run(args: Vec<String>) -> HostResult<()> {
    let argv0 = args
        .first()
        .and_then(|arg| Path::new(arg.as_str()).file_name())
        .and_then(|name| name.to_str());
    if argv0 == Some("nixos-proxnix-start-host") {
        return reconcile_phase::start_host_main(&args[1..]);
    }
    match args.get(1).map(String::as_str) {
        Some("pve-conf-to-nix") => pve_conf::main(&args[2..]),
        Some("api") => api::main(&args[2..]),
        Some("authority") => authority_main(&args[2..]),
        Some("create-lxc") => create_lxc::main(&args[2..]),
        Some("ct") => ct::main(&args[2..]),
        Some("flake-update") => flake_update::main(&args[2..]),
        Some("gc") => gc::main(&args[2..]),
        Some("reconcile") => reconcile_main(&args[2..]),
        Some("start") => reconcile_phase::start_main(&args[2..]),
        Some("start-host") => reconcile_phase::start_host_main(&args[2..]),
        Some("template") => template_main(&args[2..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some("-V") | Some("--version") => {
            println!("proxnix-host {}", version());
            Ok(())
        }
        Some(command) => Err(HostError::new(format!("unknown subcommand: {command}"))),
    }
}

fn version() -> &'static str {
    option_env!("PROXNIX_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
}

pub(crate) fn print_usage() {
    println!(
        "\
Usage:
  proxnix-host pve-conf-to-nix --pve-conf <path> --out-dir <dir>
  proxnix-host api site-updated|status|plan [options]
  proxnix-host authority render --root <dir> --authority <dir> --pve-lxc-dir <dir> --node-name <name>
  proxnix-host create-lxc [options]
  proxnix-host ct rollback --vmid <vmid>
  proxnix-host flake-update [--force] [--frequency daily|weekly|monthly|disabled] [--input <name>] [input ...]
  proxnix-host gc [--dry-run]
  proxnix-host reconcile [--dry-run] [--online] --vmid <vmid> [--node-name <name>]
  proxnix-host reconcile [--dry-run] [--online] --all-ct [--node-name <name>]
  proxnix-host reconcile --auto-tag [--node-name <name>]
  proxnix-host start --vmid <vmid> [--node-name <name>]
  proxnix-host start-host [--vmid <vmid>] [--rootfs <mounted-rootfs>]
  proxnix-host reconcile build [--vmid <vmid>]
  proxnix-host reconcile build-golden [--node-name <name>]
  proxnix-host reconcile activate [--vmid <vmid>]
  proxnix-host reconcile podman-secrets --rootfs <path> --vmid <vmid> --secrets-dir <dir>
  proxnix-host reconcile seed --vmid <vmid> [--rootfs <path>]
  proxnix-host reconcile seed-offline --vmid <vmid> --rootfs <path>
  proxnix-host template bootstrap [--template-storage <name>] [--template-name <name>] [--force] [--dry-run]
  proxnix-host --version
"
    );
}

fn authority_main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("render") => authority::render_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(HostError::new(format!(
            "unknown authority subcommand: {command}"
        ))),
    }
}

fn reconcile_main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("activate") => reconcile_phase::activate_main(&args[1..]),
        Some("build") => reconcile_phase::build_main(&args[1..]),
        Some("build-golden") => reconcile_phase::build_golden_main(&args[1..]),
        Some("podman-secrets") => podman_secrets::main(&args[1..]),
        Some("seed") => reconcile_phase::seed_main(&args[1..]),
        Some("seed-offline") => reconcile_phase::seed_offline_main(&args[1..]),
        Some("-h") | Some("--help") | None => reconcile_phase::main(args),
        Some(command) if command.starts_with('-') => reconcile_phase::main(args),
        Some(command) => Err(HostError::new(format!(
            "unknown reconcile subcommand: {command}"
        ))),
    }
}

fn template_main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("bootstrap") => template_bootstrap::main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(HostError::new(format!(
            "unknown template subcommand: {command}"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use crate::authority::render_authority;
    use crate::common::{isoformat_from_unix_parts, zulu_seconds_from_unix};
    use crate::payload_stage::{determine_host_root_uid, parse_identity_payload};
    use crate::podman_secrets::{podman_driver_options, reconcile_podman_secrets};
    use crate::pve_conf::{generate_proxmox_nix, parse_pve_conf_content, ProxmoxNixData};
    use serde_json::{json, Value};
    use std::collections::BTreeSet;
    use std::env;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process;
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
    fn renders_expected_proxmox_nix_module_shape() {
        let rendered = generate_proxmox_nix(&ProxmoxNixData {
            hostname: Some("ct${101}".to_owned()),
            nameservers: vec!["1.1.1.1".to_owned(), "8.8.8.8".to_owned()],
            search_domain: Some("example.test".to_owned()),
            ssh_keys: vec!["ssh-ed25519 AAA \"quoted\"".to_owned()],
        });

        assert_eq!(
            rendered,
            "\
# Generated by proxnix-host pve-conf-to-nix \u{2014} do not edit by hand.
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
    fn formats_utc_iso_timestamps_with_colon_offset() {
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
