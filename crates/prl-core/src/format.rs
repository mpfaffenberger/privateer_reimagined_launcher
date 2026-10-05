//! Channel pointer + manifest (docs/DESIGN.md), and the install-path rules.
//! Mirrors `publisher/src/prl/{manifest,paths}.py`; the e2e test keeps them in sync.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::{Error, Result};

pub const FORMAT: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChannelPointer {
    pub format: u32,
    pub channel: String,
    pub version: String,
    pub manifest: String,
    pub manifest_sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct FileEntry {
    pub path: String,
    pub size: u64,
    pub sha256: String,
    pub enc: String,
    pub stored_size: u64,
}

impl FileEntry {
    pub fn blob_key(&self) -> String {
        let suffix = if self.enc == "zstd" { ".zst" } else { "" };
        format!("blobs/{}/{}{}", &self.sha256[..2], self.sha256, suffix)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Manifest {
    pub format: u32,
    pub product: String,
    pub version: String,
    pub channel: String,
    pub created_utc: String,
    #[serde(default)]
    pub launch: std::collections::BTreeMap<String, String>,
    pub total_size: u64,
    pub files: Vec<FileEntry>,
}

impl Manifest {
    /// Parse and validate everything an attacker-controlled manifest could abuse.
    pub fn parse(bytes: &[u8]) -> Result<Manifest> {
        let m: Manifest = serde_json::from_slice(bytes).map_err(|e| Error::Format(e.to_string()))?;
        if m.format != FORMAT {
            return Err(Error::Format(format!("unsupported manifest format {}", m.format)));
        }
        let mut seen = HashSet::new();
        for f in &m.files {
            check_path(&f.path)?;
            if !seen.insert(f.path.to_lowercase()) {
                return Err(Error::Format(format!("duplicate path (ignoring case): {}", f.path)));
            }
            if f.sha256.len() != 64 || !f.sha256.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
                return Err(Error::Format(format!("bad sha256 for {}", f.path)));
            }
            if f.enc != "zstd" && f.enc != "raw" {
                return Err(Error::Format(format!("unknown encoding {:?} for {}", f.enc, f.path)));
            }
        }
        for exe in m.launch.values() {
            check_path(exe)?;   // every OS's entry, not just this one's (#66)
        }
        Ok(m)
    }

    pub fn launch_target(&self) -> Option<&str> {
        self.launch.get(OS).map(String::as_str)
    }
}

/// This build's OS, as named in manifests' `launch` map and in platform channel names.
pub const OS: &str = if cfg!(windows) { "windows" } else if cfg!(target_os = "macos") { "macos" } else { "linux" };

/// The channel this OS reads (#66). A release is one file list, so each OS has its own
/// channel: Windows keeps the bare name (every installed launcher reads `stable`), others
/// add their OS (`stable-macos`). The pointer and the signed manifest both name the channel,
/// so one OS's release can never be installed on another.
pub fn platform_channel(base: &str) -> String {
    platform_channel_for(base, OS)
}

fn platform_channel_for(base: &str, os: &str) -> String {
    if os == "windows" { base.to_string() } else { format!("{base}-{os}") }
}

/// Same rules as `publisher/src/prl/paths.py::check`.
pub fn check_path(path: &str) -> Result<()> {
    let bad = |why: &str| Err(Error::Format(format!("unsafe path {path:?}: {why}")));
    if path.is_empty() {
        return bad("empty");
    }
    if path.contains('\\') {
        return bad("backslash");
    }
    let b = path.as_bytes();
    if path.starts_with('/') || (b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':') {
        return bad("absolute");
    }
    if path.split('/').any(|s| s.is_empty() || s == "." || s == "..") {
        return bad("empty, '.' or '..' segment");
    }
    Ok(())
}

/// `root` joined with a checked manifest path, one component at a time.
pub fn install_path(root: &Path, path: &str) -> PathBuf {
    path.split('/').fold(root.to_path_buf(), |acc, seg| acc.join(seg))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_rules_match_the_publisher() {
        for bad in ["", "/etc/passwd", "C:/x", "c:x", "a/../b", "../x", "a//b", "./a", "a/.", "a\\b", "a/"] {
            assert!(check_path(bad).is_err(), "{bad:?} should be rejected");
        }
        assert!(check_path("assets/music/menu.wav").is_ok());
    }

    #[test]
    fn parse_rejects_traversal_case_dupes_and_bad_hashes() {
        let make = |files: &str| format!(
            r#"{{"format":1,"product":"p","version":"1","channel":"stable","created_utc":"x","launch":{{}},"total_size":0,"files":[{files}]}}"#);
        let entry = |p: &str, sha: &str| format!(r#"{{"path":"{p}","size":1,"sha256":"{sha}","enc":"raw","stored_size":1}}"#);
        let good = "a".repeat(64);
        assert!(Manifest::parse(make(&entry("ok/file", &good)).as_bytes()).is_ok());
        assert!(Manifest::parse(make(&entry("../evil", &good)).as_bytes()).is_err());
        let dupes = format!("{},{}", entry("A.png", &good), entry("a.PNG", &good));
        assert!(Manifest::parse(make(&dupes).as_bytes()).is_err());
        assert!(Manifest::parse(make(&entry("x", "nothex")).as_bytes()).is_err());
        assert!(Manifest::parse(make(&entry("x", &"A".repeat(64))).as_bytes()).is_err());
    }

    fn with_launch(launch: &str) -> String {
        format!(r#"{{"format":1,"product":"p","version":"1","channel":"stable","created_utc":"x","launch":{launch},"total_size":0,"files":[]}}"#)
    }

    #[test]
    fn every_os_launch_path_is_checked_not_just_this_ones() {
        assert!(Manifest::parse(with_launch(r#"{"windows":"game.exe","macos":"bin/game"}"#).as_bytes()).is_ok());
        for evil in [r#"{"macos":"../../bin/sh"}"#, r#"{"linux":"/usr/bin/x"}"#, r#"{"windows":"C:/x.exe"}"#] {
            assert!(Manifest::parse(with_launch(evil).as_bytes()).is_err(), "{evil} must be refused on every OS");
        }
    }

    #[test]
    fn each_os_reads_its_own_channel_and_windows_keeps_the_old_name() {
        assert_eq!(platform_channel_for("stable", "windows"), "stable");   // launchers 0.1-0.3 read this
        assert_eq!(platform_channel_for("stable", "macos"), "stable-macos");
        assert_eq!(platform_channel_for("beta", "linux"), "beta-linux");
        assert_eq!(platform_channel("stable"), platform_channel_for("stable", OS));
    }

    #[test]
    fn launch_target_is_this_oss_entry() {
        let m = Manifest::parse(with_launch(r#"{"windows":"w.exe","macos":"m","linux":"l"}"#).as_bytes()).unwrap();
        let want = match OS { "windows" => "w.exe", "macos" => "m", _ => "l" };
        assert_eq!(m.launch_target(), Some(want));
    }
}
