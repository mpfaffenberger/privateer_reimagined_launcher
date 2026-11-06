//! "Do you own Wing Commander: Privateer?" (docs/DESIGN.md, Ownership check).
//!
//! Registry entries, Steam manifests and folder names only *suggest* candidates. A folder
//! is accepted only if it contains real Privateer data: the GOG release keeps `PRIV.TRE`
//! inside the `GAME.GOG` disc image and boots `PRIV.EXE` -> `PRCD.EXE`. Matching those
//! files (not names) also rules out *Privateer 2: The Darkening*. Steam's release (appid
//! [`STEAM_APPID`]) is a DOSBox install with `PRIV.EXE` and `PRCD.EXE` at its root and the disc
//! as `GAME.bin`, so the same markers prove it.

use std::collections::{HashSet, VecDeque};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::vdf;

pub const MARKERS: &[&str] = &["GAME.GOG", "PRIV.TRE", "PRIV.EXE", "PRCD.EXE"];
const MAX_DEPTH: usize = 4;
/// Upper bound on directory entries inspected per candidate, so picking `C:\` is cheap.
const MAX_ENTRIES: usize = 20_000;
/// *Wing Commander™: Privateer* on Steam (#149).
pub const STEAM_APPID: &str = "2008760";

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Storefront {
    Gog,
    Steam,
    Folder,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
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

/// A name that may be Privateer: it says so, and isn't *Privateer 2: The Darkening*. Letters
/// and digits only, so `Wing Commander™: Privateer` and `Wing Commander ® Privateer ™` match.
fn mentions_privateer(s: &str) -> bool {
    let squashed: String = s.chars().filter(char::is_ascii_alphanumeric).collect::<String>().to_ascii_lowercase();
    squashed.contains("privateer") && !squashed.contains("privateer2") && !squashed.contains("darkening")
}

/// A path's identity, ignoring case and separators: Steam's registry says
/// `c:/program files (x86)/steam` where `libraryfolders.vdf` says `C:\Program Files (x86)\Steam`.
fn path_key(p: &Path) -> String {
    p.to_string_lossy().replace('\\', "/").trim_end_matches('/').to_ascii_lowercase()
}

/// Whether two paths name the same folder (see [`path_key`]).
pub fn same_folder(a: &Path, b: &Path) -> bool {
    path_key(a) == path_key(b)
}

/// Keep the first of each folder named more than once.
fn dedup_folders<T>(items: &mut Vec<T>, path: impl Fn(&T) -> &Path) {
    let mut seen = HashSet::new();
    items.retain(|item| seen.insert(path_key(path(item))));
}

/// Steam's libraries: those `<root>/steamapps/libraryfolders.vdf` lists, then the root itself.
/// Steam lists the root as library "0" in its canonical spelling, which is the one kept.
fn steam_libraries(steam_root: &Path) -> Vec<PathBuf> {
    let mut libraries = Vec::new();
    if let Ok(text) = std::fs::read_to_string(steam_root.join("steamapps").join("libraryfolders.vdf")) {
        if let Some(folders) = vdf::parse(&text).get("libraryfolders") {
            for (_, lib) in folders.entries() {
                // Newer format: { "path" "..." }; old format: "1" "D:\\SteamLibrary".
                if let Some(p) = lib.get("path").and_then(vdf::Value::str).or_else(|| lib.str()) {
                    libraries.push(PathBuf::from(p));
                }
            }
        }
    }
    libraries.push(steam_root.to_path_buf());
    dedup_folders(&mut libraries, |p| p);
    libraries
}

/// From an `appmanifest_*.acf`: `(is it appid STEAM_APPID, installdir)`, if it may be Privateer.
fn steam_app(manifest: &str) -> Option<(bool, String)> {
    let doc = vdf::parse(manifest);
    let app = doc.get("AppState")?;
    let field = |key| app.get(key).and_then(vdf::Value::str);
    let installdir = field("installdir").filter(|d| !d.is_empty())?;
    let by_appid = field("appid") == Some(STEAM_APPID);
    (by_appid || mentions_privateer(field("name").unwrap_or_default())).then(|| (by_appid, installdir.to_string()))
}

/// Steam: each library's `appmanifest_*.acf` for appid [`STEAM_APPID`] (first) or a name that
/// mentions Privateer -> `<library>/steamapps/common/<installdir>`.
pub fn steam_candidates(steam_root: &Path) -> Vec<PathBuf> {
    let mut found: Vec<(bool, PathBuf)> = Vec::new();
    for lib in steam_libraries(steam_root) {
        let apps = lib.join("steamapps");
        let Ok(entries) = std::fs::read_dir(&apps) else { continue };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !(name.starts_with("appmanifest_") && name.ends_with(".acf")) {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(entry.path()) else { continue };
            if let Some((by_appid, dir)) = steam_app(&text) {
                found.push((by_appid, apps.join("common").join(dir)));
            }
        }
    }
    found.sort_by_key(|(by_appid, _)| !by_appid); // stable: the appid's install first
    found.into_iter().map(|(_, path)| path).collect()
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

/// Steam's default roots in a home folder: macOS, then Linux.
#[cfg(any(not(windows), test))]
fn home_steam_roots(home: &Path) -> Vec<PathBuf> {
    vec![
        home.join("Library/Application Support/Steam"),
        home.join(".steam/steam"),
        home.join(".local/share/Steam"),
    ]
}

#[cfg(not(windows))]
fn steam_roots() -> Vec<PathBuf> {
    home_steam_roots(&std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default())
}

/// Every place Privateer might be, in order of preference: GOG first (what the launcher has
/// always picked, so an existing player's choice never moves), then Steam. Not yet validated.
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
    dedup_folders(&mut out, |(_, p)| p);
    out
}

fn validated((store, path): (Storefront, PathBuf)) -> Option<Found> {
    validate(store, &path)
}

/// The preferred candidate that really holds Privateer data.
pub fn detect() -> Option<Found> {
    candidates().into_iter().find_map(validated)
}

/// Every candidate that really holds Privateer data, preferred first: a player with both a GOG
/// and a Steam copy can choose.
pub fn detect_all() -> Vec<Found> {
    candidates().into_iter().filter_map(validated).collect()
}

/// A folder the player chose. One the launcher also found by itself keeps its storefront
/// (choosing the Steam folder still says "Steam"); any other is [`Storefront::Folder`].
pub fn identify(dir: &Path) -> Option<Found> {
    let store = candidates().into_iter().find(|(_, p)| same_folder(p, dir)).map_or(Storefront::Folder, |(s, _)| s);
    validate(store, dir)
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
    fn gog_mac_app_bundle_is_found_from_applications() {
        // The exact layout of GOG's Mac release on Mike's Mac (macOS 26, #66): the game data
        // sits inside the DOSBox app bundle, whose name has (R) and (TM) signs in it.
        let applications = tempfile::tempdir().unwrap();
        let app = applications.path().join("Wing Commander ® Privateer ™.app");
        touch(&app.join("Contents/Resources/game/GAME.GOG"));
        touch(&app.join("Contents/Resources/game/PRIV.EXE"));
        touch(&app.join("Contents/MacOS/DOSBox"));
        touch(&applications.path().join("Safari.app/Contents/Info.plist"));   // not a candidate
        let candidates = privateer_subdirs(applications.path());
        assert_eq!(candidates, vec![app.clone()]);
        let found = validate(Storefront::Gog, &candidates[0]).unwrap();
        assert_eq!(found.path, app);
        assert!(found.marker.ends_with("Contents/Resources/game/GAME.GOG") || found.marker.ends_with("Contents/Resources/game/PRIV.EXE"));
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

    // Copies of the files Steam wrote on Mike's PC (#149), the owner's SteamID blanked out.
    const STEAM_MANIFEST: &str = include_str!("../tests/fixtures/steam/appmanifest_2008760.acf");
    const OTHER_MANIFEST: &str = include_str!("../tests/fixtures/steam/appmanifest_228980.acf");
    const LIBRARY_FOLDERS: &str = include_str!("../tests/fixtures/steam/libraryfolders.vdf");

    /// A path as `libraryfolders.vdf` writes it: backslashes doubled.
    fn vdf_path(p: &Path) -> String {
        p.to_string_lossy().replace('\\', "\\\\")
    }

    /// A Steam root and a second library, shaped like the real install (#149): Privateer in the
    /// root library, a DOSBox layout with the disc as `GAME.bin`. Returns the game folder.
    fn fake_steam(steam: &Path, library: &Path) -> PathBuf {
        let apps = steam.join("steamapps");
        let folders = LIBRARY_FOLDERS.replace("{STEAM}", &vdf_path(steam)).replace("{LIBRARY}", &vdf_path(library));
        fs::create_dir_all(&apps).unwrap();
        fs::write(apps.join("libraryfolders.vdf"), folders).unwrap();
        fs::write(apps.join("appmanifest_2008760.acf"), STEAM_MANIFEST).unwrap();
        touch(&library.join("steamapps").join("common").join("Steamworks Shared").join("_CommonRedist").join("setup.exe"));
        fs::write(library.join("steamapps").join("appmanifest_228980.acf"), OTHER_MANIFEST).unwrap();
        let game = apps.join("common").join("Wing Commander Privateer");
        for file in ["GAME.bin", "PRIV.EXE", "PRCD.EXE", "PRIV.CFG", "INSTALL.EXE", "dosboxPRIV.conf",
                     "dosboxPRIV_single.conf", "DOSBOX/DOSBox.exe", "DOSBOX/Wing Commander Privateer.exe",
                     "Extras/Wing_Commander_Privateer_Manual.pdf", "SteamInput/steam_input_manifest.vdf"] {
            touch(&game.join(file));
        }
        game
    }

    #[test]
    fn steam_dosbox_install_is_found_by_appid() {
        let t = tempfile::tempdir().unwrap();
        let steam = t.path().join("Program Files (x86)").join("Steam");
        let game = fake_steam(&steam, &t.path().join("SteamLibrary"));
        assert_eq!(steam_app(STEAM_MANIFEST), Some((true, "Wing Commander Privateer".into())));
        assert_eq!(steam_app(OTHER_MANIFEST), None);
        assert_eq!(steam_candidates(&steam), vec![game.clone()]);
        let found = validate(Storefront::Steam, &game).unwrap();
        assert_eq!(found.store, Storefront::Steam);
        let marker = found.marker.file_name().unwrap().to_string_lossy().to_string();
        assert!(marker == "PRIV.EXE" || marker == "PRCD.EXE", "{marker}"); // GAME.bin isn't GAME.GOG
    }

    /// HKCU's `SteamPath` on Mike's PC is `c:/program files (x86)/steam`: the same copy, found
    /// once, under the spelling `libraryfolders.vdf` uses.
    #[cfg(windows)]
    #[test]
    fn steam_root_spelled_like_the_registry_finds_the_copy_once() {
        let t = tempfile::tempdir().unwrap();
        let steam = t.path().join("Program Files (x86)").join("Steam");
        let game = fake_steam(&steam, &t.path().join("SteamLibrary"));
        let registry = PathBuf::from(steam.to_string_lossy().to_ascii_lowercase().replace('\\', "/"));
        assert_eq!(steam_candidates(&registry), vec![game]);
    }

    #[test]
    fn folders_compare_without_case_or_separators() {
        assert!(same_folder(Path::new(r"C:\Program Files (x86)\Steam\"), Path::new("c:/program files (x86)/steam")));
        assert!(!same_folder(Path::new("C:/Steam"), Path::new("C:/Steam2")));
        let mut found = vec![PathBuf::from(r"C:\Steam\a"), PathBuf::from("c:/steam/A/"), PathBuf::from(r"C:\Steam\b")];
        dedup_folders(&mut found, |p| p);
        assert_eq!(found, vec![PathBuf::from(r"C:\Steam\a"), PathBuf::from(r"C:\Steam\b")]);
    }

    #[test]
    fn names_match_privateer_but_not_privateer_2() {
        assert!(mentions_privateer("Wing Commander™: Privateer"));
        assert!(mentions_privateer("Wing Commander ® Privateer ™.app"));
        assert!(!mentions_privateer("Privateer 2: The Darkening"));
        assert!(!mentions_privateer("Wing Commander Privateer 2 The Darkening"));
        let manifest = |id, name, dir| format!(r#""AppState" {{ "appid" "{id}" "name" "{name}" "installdir" "{dir}" }}"#);
        assert_eq!(steam_app(&manifest("1", "Wing Commander: Privateer", "WCP")), Some((false, "WCP".into())));
        assert_eq!(steam_app(&manifest("2", "Privateer 2: The Darkening", "P2")), None);
        assert_eq!(steam_app(&manifest(STEAM_APPID, "", "WCP")), Some((true, "WCP".into()))); // the appid alone
        assert_eq!(steam_app(&manifest(STEAM_APPID, "Wing Commander: Privateer", "")), None); // nowhere to look
    }

    #[test]
    fn the_appid_install_comes_before_name_matches() {
        let t = tempfile::tempdir().unwrap();
        let steam = t.path().join("Steam");
        let game = fake_steam(&steam, &t.path().join("SteamLibrary"));
        fs::write(steam.join("steamapps").join("appmanifest_1.acf"),
                  r#""AppState" { "appid" "1" "name" "Privateer Fan Remaster" "installdir" "Remaster" }"#).unwrap();
        let remaster = steam.join("steamapps").join("common").join("Remaster");
        assert_eq!(steam_candidates(&steam), vec![game, remaster]);
    }

    #[test]
    fn mac_steam_root_is_searched_first() {
        let home = tempfile::tempdir().unwrap();
        let roots = home_steam_roots(home.path());
        let mac = home.path().join("Library").join("Application Support").join("Steam");
        assert_eq!(roots[0], mac);
        let game = fake_steam(&mac, &home.path().join("SteamLibrary"));
        assert_eq!(steam_candidates(&roots[0]), vec![game]);
    }
}
