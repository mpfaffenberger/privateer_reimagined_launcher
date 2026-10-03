//! "Do you own Wing Commander: Privateer?" (docs/DESIGN.md, Ownership check).
//!
//! Registry entries, Steam manifests and folder names only *suggest* candidates. A folder
//! is accepted only if it contains real Privateer data: the GOG release keeps `PRIV.TRE`
//! inside the `GAME.GOG` disc image and boots `PRIV.EXE` -> `PRCD.EXE`. Matching those
//! files (not names) also rules out *Privateer 2: The Darkening*.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::vdf;

pub const MARKERS: &[&str] = &["GAME.GOG", "PRIV.TRE", "PRIV.EXE", "PRCD.EXE"];
const MAX_DEPTH: usize = 4;
/// Upper bound on directory entries inspected per candidate, so picking `C:\` is cheap.
const MAX_ENTRIES: usize = 20_000;

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Storefront {
    Gog,
    Steam,
    Folder,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct Found {
    pub store: Storefront,
    /// The folder that was checked (what we remember and re-check at launch).
    pub path: PathBuf,
    /// The Privateer data file that proved it.
    pub marker: PathBuf,
}

/// The first Privateer data file under `dir` (breadth-first, bounded), if any.
pub fn find_marker(dir: &Path) -> Option<PathBuf> {
    let mut queue = VecDeque::from([(dir.to_path_buf(), 0usize)]);
    let mut seen = 0usize;
    while let Some((d, depth)) = queue.pop_front() {
        let Ok(entries) = std::fs::read_dir(&d) else { continue };
        for entry in entries.flatten() {
            seen += 1;
            if seen > MAX_ENTRIES {
                return None;
            }
            let path = entry.path();
            let Ok(kind) = entry.file_type() else { continue };
            if kind.is_file() {
                let name = entry.file_name().to_string_lossy().to_ascii_uppercase();
                if MARKERS.contains(&name.as_str()) {
                    return Some(path);
                }
            } else if kind.is_dir() && depth < MAX_DEPTH {
                queue.push_back((path, depth + 1));
            }
        }
    }
    None
}

pub fn validate(store: Storefront, dir: &Path) -> Option<Found> {
    find_marker(dir).map(|marker| Found { store, path: dir.to_path_buf(), marker })
}

fn mentions_privateer(s: &str) -> bool {
    s.to_ascii_lowercase().contains("privateer")
}

/// Steam: `<root>/steamapps/libraryfolders.vdf` -> libraries -> `appmanifest_*.acf` whose
/// name mentions Privateer -> `<library>/steamapps/common/<installdir>`.
pub fn steam_candidates(steam_root: &Path) -> Vec<PathBuf> {
    let mut libraries = vec![steam_root.to_path_buf()];
    if let Ok(text) = std::fs::read_to_string(steam_root.join("steamapps").join("libraryfolders.vdf")) {
        let doc = vdf::parse(&text);
        if let Some(folders) = doc.get("libraryfolders") {
            for (_, lib) in folders.entries() {
                // Newer format: { "path" "..." }; old format: "1" "D:\\SteamLibrary".
                let path = lib.get("path").and_then(vdf::Value::str).or_else(|| lib.str());
                if let Some(p) = path {
                    libraries.push(PathBuf::from(p));
                }
            }
        }
    }
    libraries.dedup();
    let mut out = Vec::new();
    for lib in libraries {
        let apps = lib.join("steamapps");
        let Ok(entries) = std::fs::read_dir(&apps) else { continue };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !(name.starts_with("appmanifest_") && name.ends_with(".acf")) {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(entry.path()) else { continue };
            let doc = vdf::parse(&text);
            let Some(app) = doc.get("AppState") else { continue };
            let title = app.get("name").and_then(vdf::Value::str).unwrap_or_default();
            if let (true, Some(dir)) = (mentions_privateer(title), app.get("installdir").and_then(vdf::Value::str)) {
                out.push(apps.join("common").join(dir));
            }
        }
    }
    out
}

/// Sub-folders of `parent` whose name mentions Privateer (GOG default install roots).
fn privateer_subdirs(parent: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(parent) else { return Vec::new() };
    entries
        .flatten()
        .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
        .filter(|e| mentions_privateer(&e.file_name().to_string_lossy()))
        .map(|e| e.path())
        .collect()
}

#[cfg(windows)]
fn gog_registry() -> Vec<PathBuf> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let mut out = Vec::new();
    for root in [r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"] {
        let Ok(games) = hklm.open_subkey(root) else { continue };
        for id in games.enum_keys().flatten() {
            let Ok(game) = games.open_subkey(&id) else { continue };
            let name: String = game.get_value("gameName").unwrap_or_default();
            let path: String = game.get_value("path").unwrap_or_default();
            if mentions_privateer(&name) && !path.is_empty() {
                out.push(PathBuf::from(path));
            }
        }
    }
    out
}

#[cfg(windows)]
fn steam_roots() -> Vec<PathBuf> {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
    use winreg::RegKey;
    let mut out = Vec::new();
    if let Ok(k) = RegKey::predef(HKEY_CURRENT_USER).open_subkey(r"Software\Valve\Steam") {
        if let Ok(p) = k.get_value::<String, _>("SteamPath") {
            out.push(PathBuf::from(p));
        }
    }
    if let Ok(k) = RegKey::predef(HKEY_LOCAL_MACHINE).open_subkey(r"SOFTWARE\WOW6432Node\Valve\Steam") {
        if let Ok(p) = k.get_value::<String, _>("InstallPath") {
            out.push(PathBuf::from(p));
        }
    }
    out.push(PathBuf::from(r"C:\Program Files (x86)\Steam"));
    out
}

#[cfg(not(windows))]
fn steam_roots() -> Vec<PathBuf> {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    vec![
        home.join("Library/Application Support/Steam"),
        home.join(".steam/steam"),
        home.join(".local/share/Steam"),
    ]
}

/// Every place Privateer might be, most specific first. Not yet validated.
pub fn candidates() -> Vec<(Storefront, PathBuf)> {
    let mut out: Vec<(Storefront, PathBuf)> = Vec::new();
    #[cfg(windows)]
    {
        out.extend(gog_registry().into_iter().map(|p| (Storefront::Gog, p)));
        for parent in [r"C:\GOG Games", r"C:\Program Files (x86)\GOG Galaxy\Games", r"C:\Program Files\GOG Galaxy\Games"] {
            out.extend(privateer_subdirs(Path::new(parent)).into_iter().map(|p| (Storefront::Gog, p)));
        }
    }
    #[cfg(target_os = "macos")]
    {
        out.extend(privateer_subdirs(Path::new("/Applications")).into_iter().map(|p| (Storefront::Gog, p)));
    }
    for root in steam_roots() {
        out.extend(steam_candidates(&root).into_iter().map(|p| (Storefront::Steam, p)));
    }
    let mut seen = std::collections::HashSet::new();
    out.retain(|(_, p)| seen.insert(p.to_string_lossy().to_ascii_lowercase()));
    out
}

/// The first candidate that really holds Privateer data.
pub fn detect() -> Option<Found> {
    candidates().into_iter().find_map(|(store, path)| validate(store, &path))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn touch(p: &Path) {
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, b"x").unwrap();
    }

    #[test]
    fn gog_layout_with_game_gog_is_accepted() {
        let t = tempfile::tempdir().unwrap();
        let app = t.path().join("Wing Commander Privateer");
        touch(&app.join("Contents/Resources/game/game.gog")); // case-insensitive
        let found = validate(Storefront::Gog, &app).unwrap();
        assert!(found.marker.ends_with("game.gog"));
    }

    #[test]
    fn privateer_2_and_empty_folders_are_rejected() {
        let t = tempfile::tempdir().unwrap();
        let p2 = t.path().join("Privateer 2 The Darkening");
        touch(&p2.join("PRIV2.EXE"));
        touch(&p2.join("DATA/SOMETHING.DAT"));
        assert!(validate(Storefront::Gog, &p2).is_none());
        assert!(validate(Storefront::Folder, &t.path().join("missing")).is_none());
    }

    #[test]
    fn marker_beyond_max_depth_is_not_found() {
        let t = tempfile::tempdir().unwrap();
        touch(&t.path().join("a/b/c/d/e/f/PRIV.EXE"));
        assert!(find_marker(t.path()).is_none());
        touch(&t.path().join("a/b/PRCD.EXE"));
        assert!(find_marker(t.path()).is_some());
    }

    #[test]
    fn steam_libraries_and_app_manifests_resolve_install_dirs() {
        let t = tempfile::tempdir().unwrap();
        let steam = t.path().join("Steam");
        let lib2 = t.path().join("Lib2");
        let escaped = lib2.to_string_lossy().replace('\\', "\\\\");
        fs::create_dir_all(steam.join("steamapps")).unwrap();
        fs::write(steam.join("steamapps/libraryfolders.vdf"),
                  format!("\"libraryfolders\" {{ \"0\" {{ \"path\" \"{escaped}\" }} }}")).unwrap();
        fs::create_dir_all(lib2.join("steamapps/common/WCP")).unwrap();
        fs::write(lib2.join("steamapps/appmanifest_1.acf"),
                  r#""AppState" { "name" "Wing Commander: Privateer" "installdir" "WCP" }"#).unwrap();
        fs::write(lib2.join("steamapps/appmanifest_2.acf"),
                  r#""AppState" { "name" "Some Other Game" "installdir" "Other" }"#).unwrap();
        let got = steam_candidates(&steam);
        assert_eq!(got, vec![lib2.join("steamapps").join("common").join("WCP")]);
        touch(&got[0].join("PRIV.EXE"));
        assert_eq!(validate(Storefront::Steam, &got[0]).unwrap().store, Storefront::Steam);
    }
}
