use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::io::{self, BufReader};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

use crate::common::{HostError, HostResult};

pub(crate) const BUNDLE_SCHEMA: &str = "proxnix.secrets.v1";
pub(crate) const BUNDLE_FILE: &str = "effective.secrets.json";

pub(crate) fn guest_main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("ls") => {
            require_arg_count(args, 1)?;
            cmd_ls()
        }
        Some("ls-shared") => {
            require_arg_count(args, 1)?;
            cmd_ls()
        }
        Some("get") => {
            require_arg_count(args, 2)?;
            cmd_get(&args[1])
        }
        Some("get-shared") => {
            require_arg_count(args, 2)?;
            cmd_get(&args[1])
        }
        Some("podman") => podman_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            guest_usage();
            Ok(())
        }
        Some(command) => Err(HostError::new(format!(
            "unknown proxnix-secrets command: {command}"
        ))),
    }
}

fn require_arg_count(args: &[String], expected: usize) -> HostResult<()> {
    if args.len() == expected {
        Ok(())
    } else {
        guest_usage();
        Err(HostError::silent_exit(2))
    }
}

fn guest_usage() {
    eprintln!(
        "\
Usage:
  proxnix-secrets ls
  proxnix-secrets get             <name>
  proxnix-secrets get-shared      <name>   # compatibility alias for get
  proxnix-secrets ls-shared               # compatibility alias for ls
  proxnix-secrets podman          list|lookup|store|delete
"
    );
}

fn podman_main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("list") => {
            require_arg_count(args, 1)?;
            podman_list()
        }
        Some("lookup") => {
            require_arg_count(args, 1)?;
            podman_lookup()
        }
        Some("store") => Err(HostError::new(
            "podman secret store is read-only for proxnix-managed secrets; use proxnix-secrets on the workstation",
        )),
        Some("delete") => {
            require_arg_count(args, 1)?;
            podman_delete()
        }
        _ => {
            guest_usage();
            Err(HostError::silent_exit(2))
        }
    }
}

fn cmd_ls() -> HostResult<()> {
    for name in secret_names_from_bundle(&effective_bundle_path())
        .map_err(|err| HostError::new(format!("failed to read secret bundle: {err}")))?
    {
        println!("{name}\teffective");
    }
    Ok(())
}

fn cmd_get(name: &str) -> HostResult<()> {
    let value = decrypt_secret_from_bundle(&effective_bundle_path(), &identity_path(), name)
        .map_err(HostError::new)?;
    print!("{value}");
    Ok(())
}

fn podman_list() -> HostResult<()> {
    let ids_dir = podman_ids_dir();
    if !ids_dir.is_dir() {
        return Ok(());
    }
    for entry in sorted_dir_entries(&ids_dir)
        .map_err(|err| HostError::new(format!("failed to read {}: {err}", ids_dir.display())))?
    {
        if entry.path().is_file() {
            println!("{}", entry.file_name().to_string_lossy());
        }
    }
    Ok(())
}

fn podman_lookup() -> HostResult<()> {
    let secret_id = env::var("SECRET_ID").unwrap_or_default();
    let name_file = podman_ids_dir().join(&secret_id);
    let name = if name_file.is_file() {
        fs::read_to_string(&name_file)
            .map_err(|err| {
                HostError::new(format!("failed to read {}: {err}", name_file.display()))
            })?
            .trim_end_matches('\n')
            .to_owned()
    } else {
        secret_id
    };
    if name.is_empty() {
        return Err(HostError::new("secret id not found: SECRET_ID is unset"));
    }
    cmd_get(&name)
}

fn podman_delete() -> HostResult<()> {
    if let Ok(secret_id) = env::var("SECRET_ID") {
        if !secret_id.is_empty() {
            let path = podman_ids_dir().join(secret_id);
            match fs::remove_file(&path) {
                Ok(()) => {}
                Err(err) if err.kind() == io::ErrorKind::NotFound => {}
                Err(err) => {
                    return Err(HostError::new(format!(
                        "failed to remove {}: {err}",
                        path.display()
                    )))
                }
            }
        }
    }
    Ok(())
}

fn effective_bundle_path() -> PathBuf {
    env::var_os("PROXNIX_GUEST_SECRET_BUNDLE")
        .map(PathBuf::from)
        .unwrap_or_else(|| guest_secret_dir().join(BUNDLE_FILE))
}

fn identity_path() -> PathBuf {
    env::var_os("PROXNIX_GUEST_SECRET_IDENTITY")
        .map(PathBuf::from)
        .unwrap_or_else(|| guest_secret_dir().join("identity"))
}

fn guest_secret_dir() -> PathBuf {
    env::var_os("PROXNIX_GUEST_SECRET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/var/lib/proxnix/secrets"))
}

fn podman_ids_dir() -> PathBuf {
    env::var_os("PROXNIX_IDS_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/etc/secrets/.ids"))
}

pub(crate) fn secret_names_from_bundle(path: &Path) -> io::Result<BTreeSet<String>> {
    if !path.exists() {
        return Ok(BTreeSet::new());
    }
    let value = load_bundle_value(path)?;
    bundle_secret_names(&value)
}

pub(crate) fn decrypt_secret_from_bundle(
    bundle_path: &Path,
    identity_path: &Path,
    name: &str,
) -> Result<String, String> {
    let value = load_bundle_value(bundle_path)
        .map_err(|err| format!("failed to read {}: {err}", bundle_path.display()))?;
    let ciphertext = bundle_secret_ciphertext(&value, name)?;
    let identity = fs::read_to_string(identity_path)
        .map_err(|err| format!("failed to read {}: {err}", identity_path.display()))?;
    decrypt_age_text(ciphertext, &identity)
}

pub(crate) fn decrypt_age_text(ciphertext: &str, identity_text: &str) -> Result<String, String> {
    let identity = age::ssh::Identity::from_buffer(
        BufReader::new(identity_text.as_bytes()),
        Some("proxnix identity".to_owned()),
    )
    .map_err(|err| format!("failed to parse SSH age identity: {err}"))?;
    let plaintext = age::decrypt(&identity, ciphertext.as_bytes())
        .map_err(|err| format!("failed to decrypt age payload: {err}"))?;
    String::from_utf8(plaintext).map_err(|err| format!("secret is not UTF-8: {err}"))
}

fn load_bundle_value(path: &Path) -> io::Result<Value> {
    let content = fs::read_to_string(path)?;
    serde_json::from_str::<Value>(&content)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))
}

fn bundle_secret_names(value: &Value) -> io::Result<BTreeSet<String>> {
    let secrets = validated_secrets(value)?;
    Ok(secrets.keys().cloned().collect())
}

fn bundle_secret_ciphertext<'a>(value: &'a Value, name: &str) -> Result<&'a str, String> {
    let secrets = validated_secrets(value).map_err(|err| err.to_string())?;
    let entry = secrets
        .get(name)
        .ok_or_else(|| format!("secret not found: {name}"))?;
    entry
        .get("age")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("secret {name} has no age ciphertext"))
}

fn validated_secrets(value: &Value) -> io::Result<&Map<String, Value>> {
    if value.get("schema").and_then(Value::as_str) != Some(BUNDLE_SCHEMA) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid proxnix secret bundle schema",
        ));
    }
    value
        .get("secrets")
        .and_then(Value::as_object)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "secret bundle has no secrets object",
            )
        })
}

fn sorted_dir_entries(path: &Path) -> io::Result<Vec<fs::DirEntry>> {
    let mut entries = fs::read_dir(path)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::path::{Path, PathBuf};

    struct TestTemp {
        path: PathBuf,
    }

    impl TestTemp {
        fn new() -> Self {
            let mut path = std::env::temp_dir();
            path.push(format!(
                "proxnix-secret-bundle-test-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }

        fn path(&self) -> &Path {
            &self.path
        }
    }

    impl Drop for TestTemp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn lists_names_from_self_describing_bundle() {
        let value = serde_json::json!({
            "schema": BUNDLE_SCHEMA,
            "secrets": {
                "beta": { "age": "ciphertext" },
                "alpha": { "age": "ciphertext" }
            }
        });

        assert_eq!(
            bundle_secret_names(&value).unwrap(),
            BTreeSet::from(["alpha".to_owned(), "beta".to_owned()])
        );
    }

    #[test]
    fn decrypts_armored_age_secret_with_ssh_identity() {
        let tmp = TestTemp::new();
        let key = tmp.path().join("identity");
        std::process::Command::new("ssh-keygen")
            .arg("-q")
            .arg("-t")
            .arg("ed25519")
            .arg("-N")
            .arg("")
            .arg("-f")
            .arg(&key)
            .status()
            .unwrap();
        let public = fs::read_to_string(key.with_extension("pub")).unwrap();

        let mut child = std::process::Command::new("age")
            .arg("--encrypt")
            .arg("--armor")
            .arg("-r")
            .arg(public.trim())
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .as_mut()
            .unwrap()
            .write_all(b"secret-value")
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(output.status.success());
        let ciphertext = String::from_utf8(output.stdout).unwrap();
        let identity = fs::read_to_string(key).unwrap();

        assert_eq!(
            decrypt_age_text(&ciphertext, &identity).unwrap(),
            "secret-value"
        );
    }
}
