//! Privateer Reimagined launcher engine. Formats and guarantees: `docs/DESIGN.md`.
//!
//! Flow: [`install::Installer::check`] (verified release) ->
//! [`install::Installer::plan`] (what to download) -> [`install::Installer::apply`].
//! [`ownership::detect`] gates installing on owning *Wing Commander: Privateer*.

pub mod format;
pub mod install;
pub mod keys;
pub mod ownership;
pub mod source;
pub mod verify;
pub mod vdf;

/// Everything that can go wrong, as user-presentable messages.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("network or storage error: {0}")]
    Fetch(String),
    #[error("release check failed: {0}")]
    Untrusted(String),
    #[error("bad release data: {0}")]
    Format(String),
    #[error("file error at {path}: {source}")]
    Io { path: String, source: std::io::Error },
}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn io_err(path: &std::path::Path) -> impl FnOnce(std::io::Error) -> Error + '_ {
    move |source| Error::Io { path: path.display().to_string(), source }
}
