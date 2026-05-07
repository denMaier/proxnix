mod common {
    use std::fmt;

    pub(crate) type HostResult<T> = Result<T, HostError>;

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
}
#[path = "../secret_bundle.rs"]
mod secret_bundle;

fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if let Err(err) = secret_bundle::guest_main(&args) {
        if !err.message().is_empty() {
            eprintln!("error: {err}");
        }
        std::process::exit(err.exit_code());
    }
}
