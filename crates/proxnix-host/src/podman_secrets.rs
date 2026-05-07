use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process;

use serde_json::{json, Map, Value};
use uuid::Uuid;

use crate::common::{set_mode, utc_now_isoformat, HostResult};
use crate::print_usage;
use crate::secret_bundle::{secret_names_from_bundle, BUNDLE_FILE};

const PODMAN_LABEL_KEY: &str = "proxnix.managed";

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    let mut rootfs: Option<PathBuf> = None;
    let mut vmid: Option<String> = None;
    let mut secrets_dir: Option<PathBuf> = None;

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--rootfs" => {
                index += 1;
                rootfs = args.get(index).map(PathBuf::from);
            }
            "--vmid" => {
                index += 1;
                vmid = args.get(index).cloned();
            }
            "--secrets-dir" => {
                index += 1;
                secrets_dir = args.get(index).map(PathBuf::from);
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown podman-secrets argument: {other}").into()),
        }
        index += 1;
    }

    reconcile_podman_secrets(
        &rootfs.ok_or("--rootfs is required")?,
        &vmid.ok_or("--vmid is required")?,
        &secrets_dir.ok_or("--secrets-dir is required")?,
    )
    .map_err(|err| format!("failed to reconcile Podman secrets: {err}"))?;
    Ok(())
}

pub(crate) fn reconcile_podman_secrets(
    rootfs: &Path,
    vmid: &str,
    secrets_dir: &Path,
) -> io::Result<()> {
    let live_names: BTreeSet<String> = if secrets_dir.is_dir() {
        secret_names_from_bundle(&secrets_dir.join(BUNDLE_FILE))?
    } else {
        BTreeSet::new()
    };

    let secrets_json_path = rootfs.join("var/lib/containers/storage/secrets/secrets.json");
    let ids_dir = rootfs.join("etc/secrets/.ids");

    fs::create_dir_all(&ids_dir)?;
    let secrets_json_dir = secrets_json_path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "secrets.json has no parent"))?;
    fs::create_dir_all(secrets_json_dir)?;
    set_mode(secrets_json_dir, 0o700)?;

    let mut data = load_json_object(&secrets_json_path);
    ensure_object_field(&mut data, "secrets");
    ensure_object_field(&mut data, "nameToID");
    ensure_object_field(&mut data, "idToName");

    let now = utc_now_isoformat();
    let mut changed = false;

    let stale_ids = data
        .get("secrets")
        .and_then(Value::as_object)
        .map(|secrets| {
            secrets
                .iter()
                .filter_map(|(sid, entry)| {
                    let entry = entry.as_object()?;
                    let labels = entry.get("labels")?.as_object()?;
                    let managed =
                        labels.get(PODMAN_LABEL_KEY).and_then(Value::as_str) == Some("true");
                    let name = entry.get("name").and_then(Value::as_str).unwrap_or("");
                    if managed && !live_names.contains(name) {
                        Some((sid.clone(), name.to_owned()))
                    } else {
                        None
                    }
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    for (sid, name) in stale_ids {
        object_field_mut(&mut data, "secrets").remove(&sid);
        object_field_mut(&mut data, "nameToID").remove(&name);
        object_field_mut(&mut data, "idToName").remove(&sid);
        match fs::remove_file(ids_dir.join(&sid)) {
            Ok(()) => {}
            Err(err) if err.kind() == io::ErrorKind::NotFound => {}
            Err(err) => return Err(err),
        }
        eprintln!("[proxnix-mount][{vmid}] Unregistered removed secret: {name}");
        changed = true;
    }

    for name in &live_names {
        let sid = podman_secret_id(vmid, name);
        let created_at = data
            .get("secrets")
            .and_then(Value::as_object)
            .and_then(|secrets| secrets.get(&sid))
            .and_then(Value::as_object)
            .and_then(|entry| entry.get("createdAt"))
            .and_then(Value::as_str)
            .unwrap_or(&now)
            .to_owned();

        let entry = json!({
            "name": name,
            "id": sid,
            "labels": { PODMAN_LABEL_KEY: "true" },
            "metadata": {},
            "createdAt": created_at,
            "updatedAt": now,
            "driver": "shell",
            "driverOptions": podman_driver_options(),
        });

        if data
            .get("secrets")
            .and_then(Value::as_object)
            .and_then(|secrets| secrets.get(&sid))
            != Some(&entry)
        {
            object_field_mut(&mut data, "secrets").insert(sid.clone(), entry);
            changed = true;
        }

        if data
            .get("nameToID")
            .and_then(Value::as_object)
            .and_then(|name_to_id| name_to_id.get(name))
            != Some(&json!(sid.clone()))
        {
            object_field_mut(&mut data, "nameToID").insert(name.to_owned(), json!(sid.clone()));
            changed = true;
        }
        if data
            .get("idToName")
            .and_then(Value::as_object)
            .and_then(|id_to_name| id_to_name.get(&sid))
            != Some(&json!(name))
        {
            object_field_mut(&mut data, "idToName").insert(sid.clone(), json!(name));
            changed = true;
        }

        let ids_file = ids_dir.join(&sid);
        if fs::read_to_string(&ids_file)
            .map(|existing| existing != *name)
            .unwrap_or(true)
        {
            fs::write(&ids_file, name)?;
            set_mode(&ids_file, 0o640)?;
        }
    }

    if changed {
        let tmp_path = secrets_json_path.with_file_name(format!(
            ".{}.tmp.{}",
            secrets_json_path
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("secrets.json"),
            process::id()
        ));
        let rendered = serde_json::to_string_pretty(&Value::Object(data))
            .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?
            + "\n";
        fs::write(&tmp_path, rendered)?;
        set_mode(&tmp_path, 0o600)?;
        fs::rename(&tmp_path, &secrets_json_path)?;
        eprintln!(
            "[proxnix-mount][{vmid}] Podman secrets.json reconciled ({} secret(s))",
            live_names.len()
        );
    }

    Ok(())
}

fn podman_secret_id(vmid: &str, name: &str) -> String {
    Uuid::new_v5(
        &Uuid::NAMESPACE_DNS,
        format!("proxnix:{vmid}:{name}").as_bytes(),
    )
    .simple()
    .to_string()
}

pub(crate) fn podman_driver_options() -> Value {
    json!({
        "store": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman store",
        "lookup": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman lookup",
        "list": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman list",
        "delete": "/var/lib/proxnix/runtime/bin/proxnix-secrets podman delete",
    })
}

fn load_json_object(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

fn ensure_object_field(data: &mut Map<String, Value>, field: &str) {
    if !data.get(field).is_some_and(Value::is_object) {
        data.insert(field.to_owned(), Value::Object(Map::new()));
    }
}

fn object_field_mut<'a>(
    data: &'a mut Map<String, Value>,
    field: &str,
) -> &'a mut Map<String, Value> {
    data.get_mut(field)
        .and_then(Value::as_object_mut)
        .expect("object field was initialized")
}
