use crate::common::{take_arg, valid_vmid, HostError, HostResult};
use crate::reconcile_phase;

pub(crate) fn main(args: &[String]) -> HostResult<()> {
    match args.first().map(String::as_str) {
        Some("rollback") => rollback_main(&args[1..]),
        Some("-h") | Some("--help") | None => {
            print_usage();
            Ok(())
        }
        Some(command) => Err(HostError::new(format!("unknown ct subcommand: {command}"))),
    }
}

fn print_usage() {
    eprintln!(
        "\
Usage:
  proxnix-host ct rollback --vmid <id> [--start-stopped]
"
    );
}

fn rollback_main(args: &[String]) -> HostResult<()> {
    let options = parse_rollback_args(args)?;
    reconcile_phase::rollback_main(&rollback_reconcile_args(&options))
}

fn rollback_reconcile_args(options: &RollbackOptions) -> Vec<String> {
    let mut args = vec!["--vmid".to_owned(), options.vmid.clone()];
    if options.start_stopped {
        args.push("--start-stopped".to_owned());
    }
    args
}

struct RollbackOptions {
    vmid: String,
    start_stopped: bool,
}

fn parse_rollback_args(args: &[String]) -> HostResult<RollbackOptions> {
    let mut vmid = None;
    let mut start_stopped = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--vmid" => vmid = Some(take_arg(args, &mut index, "--vmid")?),
            "--start-stopped" => start_stopped = true,
            "-h" | "--help" => {
                print_usage();
                return Err(HostError::silent_exit(0));
            }
            other => {
                return Err(HostError::new(format!(
                    "unknown ct rollback argument: {other}"
                )))
            }
        }
        index += 1;
    }
    let vmid = require_valid_vmid(vmid)?;
    Ok(RollbackOptions {
        vmid,
        start_stopped,
    })
}

fn require_valid_vmid(vmid: Option<String>) -> HostResult<String> {
    let vmid = vmid.ok_or_else(|| HostError::new("--vmid is required"))?;
    if !valid_vmid(&vmid) {
        return Err(HostError::new(format!("invalid VMID: {vmid}")));
    }
    Ok(vmid)
}
