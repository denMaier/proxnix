use crate::common::{take_arg, valid_vmid, HostError, HostResult};
use crate::reconcile_phase;

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("site-updated") | Some("notify") => site_updated_main(&args[1..]),
        Some("status") => status_main(&args[1..]),
        Some("plan") => plan_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(HostError::new(format!("unknown api subcommand: {command}"))),
    }
}

fn print_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host api site-updated [--node-name <name>]
  proxnix-host api status [--vmid <id>]
  proxnix-host api plan (--vmid <id>|--all-ct)

Workstation-facing host API. The workstation publishes the site repo and then
notifies the host. The host decides what to build, copy, or activate from
host-side policy such as Proxmox tags.
"
    );
}

fn site_updated_main(args: &[String]) -> HostResult<()> {
    let mut reconcile_args = vec!["--auto-tag".to_owned()];
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--node-name" => {
                let value = take_arg(args, &mut index, "--node-name")?;
                reconcile_args.push("--node-name".to_owned());
                reconcile_args.push(value);
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => {
                return Err(HostError::new(format!(
                    "unknown api site-updated argument: {other}"
                )))
            }
        }
        index += 1;
    }
    reconcile_phase::main(&reconcile_args)
}

fn status_main(args: &[String]) -> HostResult<()> {
    let mut reconcile_args = vec!["--status".to_owned()];
    push_common_query_args(args, &mut reconcile_args, "api status").map_err(HostError::new)?;
    reconcile_phase::main(&reconcile_args)
}

fn plan_main(args: &[String]) -> HostResult<()> {
    let mut reconcile_args = vec!["--dry-run".to_owned()];
    let target_seen =
        push_common_query_args(args, &mut reconcile_args, "api plan").map_err(HostError::new)?;
    if !target_seen {
        return Err(HostError::new("api plan requires --vmid <id> or --all-ct"));
    }
    reconcile_phase::main(&reconcile_args)
}

fn push_common_query_args(
    args: &[String],
    reconcile_args: &mut Vec<String>,
    context: &str,
) -> Result<bool, String> {
    let mut target_seen = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => {
                let value = take_arg(args, &mut index, "--vmid")?;
                if !valid_vmid(&value) {
                    return Err(format!("invalid VMID: {value}"));
                }
                target_seen = true;
                reconcile_args.push("--vmid".to_owned());
                reconcile_args.push(value);
            }
            "--all-ct" => {
                target_seen = true;
                reconcile_args.push("--all-ct".to_owned());
            }
            "--node-name" => {
                let value = take_arg(args, &mut index, "--node-name")?;
                reconcile_args.push("--node-name".to_owned());
                reconcile_args.push(value);
            }
            "-h" | "--help" => {
                return Err(format!(
                    "{context} help is available from proxnix-host api --help"
                ));
            }
            other => return Err(format!("unknown {context} argument: {other}")),
        }
        index += 1;
    }
    Ok(target_seen)
}

#[cfg(test)]
mod tests {
    use super::push_common_query_args;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn query_args_accept_vmid() {
        let mut out = vec!["--status".to_owned()];
        let target =
            push_common_query_args(&strings(&["--vmid", "101"]), &mut out, "api status").unwrap();
        assert!(target);
        assert_eq!(out, strings(&["--status", "--vmid", "101"]));
    }

    #[test]
    fn query_args_accept_all_ct() {
        let mut out = vec!["--dry-run".to_owned()];
        let target = push_common_query_args(&strings(&["--all-ct"]), &mut out, "api plan").unwrap();
        assert!(target);
        assert_eq!(out, strings(&["--dry-run", "--all-ct"]));
    }

    #[test]
    fn query_args_reject_invalid_vmid() {
        let mut out = vec!["--status".to_owned()];
        assert!(
            push_common_query_args(&strings(&["--vmid", "abc"]), &mut out, "api status").is_err()
        );
    }
}
