use std::env;
use std::fmt;
use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub(crate) type HostResult<T> = Result<T, HostError>;

pub(crate) const DEFAULT_PROXNIX_DIR: &str = "/var/lib/proxnix";
pub(crate) const DEFAULT_PVE_LXC_DIR: &str = "/etc/pve/lxc";
pub(crate) const GUEST_PROXNIX_DIR: &str = "var/lib/proxnix";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct HostError {
    message: String,
    exit_code: i32,
}

impl HostError {
    pub(crate) fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            exit_code: 1,
        }
    }

    pub(crate) fn silent_exit(exit_code: i32) -> Self {
        Self {
            message: String::new(),
            exit_code,
        }
    }

    pub(crate) fn message(&self) -> &str {
        &self.message
    }

    pub(crate) fn exit_code(&self) -> i32 {
        self.exit_code
    }
}

impl fmt::Display for HostError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.message.fmt(formatter)
    }
}

impl From<String> for HostError {
    fn from(message: String) -> Self {
        Self::new(message)
    }
}

impl From<&str> for HostError {
    fn from(message: &str) -> Self {
        Self::new(message)
    }
}

pub(crate) fn env_path(name: &str, default: &str) -> PathBuf {
    env::var_os(name)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(default))
}

pub(crate) fn env_bool(name: &str) -> bool {
    env::var(name).map(|value| value == "1").unwrap_or(false)
}

pub(crate) fn take_arg(args: &[String], index: &mut usize, flag: &str) -> Result<String, String> {
    *index += 1;
    args.get(*index)
        .cloned()
        .ok_or_else(|| format!("{flag} requires a value"))
}

pub(crate) fn valid_vmid(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
}

pub(crate) fn find_in_path(command: &str) -> Option<PathBuf> {
    if command.contains('/') {
        let path = PathBuf::from(command);
        return path.exists().then_some(path);
    }
    env::var_os("PATH").and_then(|path| {
        env::split_paths(&path)
            .map(|dir| dir.join(command))
            .find(|path| path.is_file())
    })
}

pub(crate) fn require_in_path(command: &str) -> Result<PathBuf, String> {
    find_in_path(command).ok_or_else(|| format!("{command} not found"))
}

pub(crate) fn require_nix() -> Result<PathBuf, String> {
    require_in_path("nix")
}

pub(crate) fn require_nix_store() -> Result<PathBuf, String> {
    require_in_path("nix-store")
}

pub(crate) fn require_pct() -> Result<PathBuf, String> {
    require_in_path("pct")
}

pub(crate) fn require_socat() -> Result<PathBuf, String> {
    require_in_path("socat")
}

pub(crate) fn set_mode(path: &Path, mode: u32) -> io::Result<()> {
    fs::set_permissions(path, fs::Permissions::from_mode(mode))
}

pub(crate) fn remove_path_if_exists(path: &Path) -> io::Result<()> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(());
    };
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

pub(crate) fn remove_file_if_exists(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err),
    }
}

#[cfg(unix)]
pub(crate) fn effective_uid() -> u32 {
    unix::unistd::geteuid().as_raw()
}

pub(crate) fn default_node_name() -> String {
    unix_hostname()
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "localhost".to_owned())
}

#[cfg(unix)]
fn unix_hostname() -> Option<String> {
    unix::unistd::gethostname()
        .ok()
        .and_then(|name| name.into_string().ok())
        .and_then(|name| name.trim().split('.').next().map(str::to_owned))
}

#[cfg(not(unix))]
fn unix_hostname() -> Option<String> {
    None
}

pub(crate) fn utc_now_isoformat() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    isoformat_from_unix_parts(now.as_secs() as i64, now.subsec_micros())
}

pub(crate) fn utc_now_seconds_z() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    zulu_seconds_from_unix(now.as_secs() as i64)
}

pub(crate) fn isoformat_from_unix_parts(seconds: i64, micros: u32) -> String {
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00")
}

pub(crate) fn zulu_seconds_from_unix(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let seconds_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    let hour = seconds_of_day / 3_600;
    let minute = (seconds_of_day % 3_600) / 60;
    let second = seconds_of_day % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

fn civil_from_days(days_since_epoch: i64) -> (i64, i64, i64) {
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    let year = y + if month <= 2 { 1 } else { 0 };
    (year, month, day)
}
