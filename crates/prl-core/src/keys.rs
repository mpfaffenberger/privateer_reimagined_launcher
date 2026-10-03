//! The release signing public key compiled into the launcher.
//!
//! Generate the key pair ONCE with `uv run prl keygen` (publisher/README.md), keep the
//! private key offline, and paste the printed base64 public key here. While this is empty,
//! the launcher refuses every release ("no release key configured"), which fails safe.

pub const RELEASE_PUBLIC_KEY: &str = "";
