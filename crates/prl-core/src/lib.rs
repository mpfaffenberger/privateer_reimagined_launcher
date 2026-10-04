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
    /// The server answered, and the file isn't there (HTTP 404, or missing from a folder).
    #[error("not on the server: {0}")]
    NotFound(String),
    /// Nothing has been published on this channel yet, e.g. no release for this OS (#66).
    /// A normal state, not a fault: the launcher says so instead of blaming the network.
    #[error("no release has been published on {0} yet")]
    NoRelease(String),
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
