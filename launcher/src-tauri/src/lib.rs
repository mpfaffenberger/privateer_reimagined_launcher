//! Tauri commands over prl-core (#4). The UI only renders state; every rule lives here:
//! the ownership gate, the release key, one operation at a time.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use prl_core::install::{sha256_hex, Installer, Mode, Progress, Release};
use prl_core::ownership::{self, Found, Storefront};
use prl_core::source::Source;
use serde::{Deserialize, Serialize};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, State, Url, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::{Update, UpdaterExt};

const DEFAULT_CDN: &str = "https://cdn.privateer-reimagined.com";
const CHANNEL: &str = "stable";
/// The news panel (#44): a page on the site, shown in a script-less sandboxed iframe, so news
/// can change without a launcher release. Its origin must match `frame-src` in tauri.conf.json.
const NEWS_URL: &str = "https://privateer-reimagined.com/launcher/";
const NEWS_TIMEOUT: Duration = Duration::from_secs(4);

#[derive(Default, Serialize, Deserialize)]
struct Config {
    /// Where Privateer was found last time (re-validated on every use).
    privateer: Option<Found>,
    install_dir: Option<PathBuf>,
}

struct AppState {
    config: Mutex<Config>,
    config_path: PathBuf,
    default_install_dir: PathBuf,
    release: Mutex<Option<Release>>,
    /// A launcher update found by check_launcher_update, waiting for the player's go (#48).
    launcher_update: Mutex<Option<Update>>,
    busy: AtomicBool,
}

impl AppState {
    fn save(&self) -> Result<(), String> {
        let cfg = self.config.lock().unwrap();
        if let Some(dir) = self.config_path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        std::fs::write(&self.config_path, serde_json::to_vec_pretty(&*cfg).unwrap()).map_err(|e| e.to_string())
    }

    fn install_dir(&self) -> PathBuf {
        self.config.lock().unwrap().install_dir.clone().unwrap_or_else(|| self.default_install_dir.clone())
    }

    fn installer(&self) -> Installer {
        Installer::new(Source::from_spec(&cdn()), public_key(), self.install_dir())
    }

    /// The remembered Privateer folder if it still holds Privateer data, else auto-detect.
    fn ownership(&self) -> Option<Found> {
        let remembered = self.config.lock().unwrap().privateer.clone();
        if let Some(found) = remembered.and_then(|f| ownership::validate(f.store, &f.path)) {
            return Some(found);
        }
        let found = ownership::detect()?;
        self.remember(&found);
        Some(found)
    }

    fn remember(&self, found: &Found) {
        self.config.lock().unwrap().privateer = Some(found.clone());
        let _ = self.save();
    }
}

fn cdn() -> String {
    std::env::var("PRL_CDN").unwrap_or_else(|_| DEFAULT_CDN.to_string())
}

/// Release builds ONLY trust the compiled-in key. Debug builds may override it so a
/// developer can test against a local store signed with a scratch key.
fn public_key() -> String {
    if cfg!(debug_assertions) {
        if let Ok(k) = std::env::var("PRL_PUBLIC_KEY") {
            return k;
        }
    }
    prl_core::keys::RELEASE_PUBLIC_KEY.to_string()
}

/// Clears the busy flag however the operation ends.
struct BusyGuard<'a>(&'a AtomicBool);
impl Drop for BusyGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}

fn begin(state: &AppState) -> Result<BusyGuard<'_>, String> {
    if state.busy.swap(true, Ordering::SeqCst) {
        return Err("Another operation is already running.".into());
    }
    Ok(BusyGuard(&state.busy))
}

#[derive(Serialize)]
struct Status {
    owned: Option<Found>,
    install_dir: PathBuf,
    installed_version: Option<String>,
    cdn: String,
}

#[tauri::command]
async fn get_status(state: State<'_, AppState>) -> Result<Status, String> {
    Ok(Status {
        owned: state.ownership(),
        install_dir: state.install_dir(),
        installed_version: state.installer().installed().map(|m| m.version),
        cdn: cdn(),
    })
}

#[tauri::command]
async fn set_privateer_folder(path: String, state: State<'_, AppState>) -> Result<Found, String> {
    let found = ownership::validate(Storefront::Folder, &PathBuf::from(&path)).ok_or_else(|| {
        "That folder doesn't contain Wing Commander: Privateer (looked for GAME.GOG, PRIV.TRE, PRIV.EXE or PRCD.EXE).".to_string()
    })?;
    state.remember(&found);
    Ok(found)
}

#[tauri::command]
async fn set_install_dir(path: String, state: State<'_, AppState>) -> Result<PathBuf, String> {
    let _busy = begin(&state)?;
    let dir = PathBuf::from(path).join("Privateer Reimagined");
    state.config.lock().unwrap().install_dir = Some(dir.clone());
    state.save()?;
    *state.release.lock().unwrap() = None;
    Ok(dir)
}

#[derive(Serialize)]
struct UpdateInfo {
    /// False when nothing is published for this OS yet (#66): not an error, just nothing to get.
    published: bool,
    version: String,
    installed_version: Option<String>,
    up_to_date: bool,
    download_bytes: u64,
    files_to_write: usize,
    files_to_remove: usize,
}

#[tauri::command]
async fn check_update(repair: bool, state: State<'_, AppState>) -> Result<UpdateInfo, String> {
    let _busy = begin(&state)?;
    let installer = Arc::new(state.installer());
    // This OS's channel: `stable` on Windows, `stable-macos` on a Mac (#66).
    let release = match installer.check(&prl_core::format::platform_channel(CHANNEL)).await {
        Err(prl_core::Error::NoRelease(_)) => {
            *state.release.lock().unwrap() = None;
            return Ok(UpdateInfo {
                published: false,
                version: String::new(),
                installed_version: installer.installed().map(|m| m.version),
                up_to_date: false,
                download_bytes: 0,
                files_to_write: 0,
                files_to_remove: 0,
            });
        }
        other => other.map_err(|e| e.to_string())?,
    };
    let (inst, rel) = (installer.clone(), release.clone());
    let mode = if repair { Mode::Repair } else { Mode::Update };
    let plan = tokio::task::spawn_blocking(move || inst.plan(&rel, mode))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?;
    let info = UpdateInfo {
        published: true,
        version: release.manifest.version.clone(),
        installed_version: installer.installed().map(|m| m.version),
        up_to_date: plan.up_to_date,
        download_bytes: plan.download_bytes,
        files_to_write: plan.place.len(),
        files_to_remove: plan.remove.len(),
    };
    *state.release.lock().unwrap() = Some(release);
    Ok(info)
}

#[tauri::command]
async fn install(repair: bool, app: AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    let _busy = begin(&state)?;
    // The ownership gate is enforced here, not just in the UI.
    if state.ownership().is_none() {
        return Err("Wing Commander: Privateer wasn't found. Point the launcher at your GOG or Steam copy first.".into());
    }
    let installer = Arc::new(state.installer());
    let release = match state.release.lock().unwrap().clone() {
        Some(r) => r,
        None => return Err("Check for updates first.".into()),
    };
    let (inst, rel) = (installer.clone(), release.clone());
    let mode = if repair { Mode::Repair } else { Mode::Update };
    let plan = tokio::task::spawn_blocking(move || inst.plan(&rel, mode))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?;
    let emitter = app.clone();
    installer
        .apply(&release, &plan, Arc::new(move |p: Progress| {
            let _ = emitter.emit("progress", p);
        }))
        .await
        .map_err(|e| e.to_string())?;
    Ok(release.manifest.version)
}

#[derive(Serialize)]
struct LauncherUpdate {
    version: String,
    notes: Option<String>,
}

/// Ask the update feed (tauri.conf.json plugins.updater) whether a newer launcher exists.
#[tauri::command]
async fn check_launcher_update(app: AppHandle, state: State<'_, AppState>) -> Result<Option<LauncherUpdate>, String> {
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let info = update.as_ref().map(|u| LauncherUpdate { version: u.version.clone(), notes: u.body.clone() });
    *state.launcher_update.lock().unwrap() = update;
    Ok(info)
}

#[derive(Clone, Serialize)]
struct LauncherProgress {
    done: u64,
    total: Option<u64>,
}

/// Download the pending launcher update, verify its signature against the compiled-in key,
/// run its installer (passive) and restart. Shares the busy lock with game installs, so a
/// launcher update can never interrupt one.
#[tauri::command]
async fn install_launcher_update(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let _busy = begin(&state)?;
    // Cloned, not taken: if this attempt fails (network, bad signature), the button can retry.
    let update = state.launcher_update.lock().unwrap().clone().ok_or("No launcher update is waiting.")?;
    let emitter = app.clone();
    let mut done = 0u64;
    update
        .download_and_install(
            move |chunk, total| {
                done += chunk as u64;
                let _ = emitter.emit("launcher-progress", LauncherProgress { done, total });
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    app.restart()
}

/// The live release's identity (its manifest hash), or None when nothing is published for this
/// OS: the cheap "anything new?" probe of a launcher left open (#78). It reads only the channel
/// pointer, so it doesn't take the one-operation lock and never blocks an install; whatever gets
/// installed still comes from check_update's fully verified release.
#[tauri::command]
async fn release_hint(state: State<'_, AppState>) -> Result<Option<String>, String> {
    match state.installer().pointer(&prl_core::format::platform_channel(CHANNEL)).await {
        Ok(pointer) => Ok(Some(pointer.manifest_sha256)),
        Err(prl_core::Error::NoRelease(_)) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

#[derive(Serialize)]
struct NewsPage {
    url: &'static str,
    /// SHA-256 of the page: a launcher left open reloads the panel only when the news changed.
    digest: String,
}

/// The news page if it answers 200 now, else None: the UI keeps its built-in fallback (instead
/// of WebView2's "can't reach this page"), or the news it already shows (#78).
#[tauri::command]
async fn news_page() -> Option<NewsPage> {
    let client = reqwest::Client::builder().timeout(NEWS_TIMEOUT).build().ok()?;
    let response = client.get(NEWS_URL).send().await.ok()?;
    if !response.status().is_success() {
        return None;
    }
    let body = response.bytes().await.ok()?;   // NEWS_TIMEOUT covers the body too
    Some(NewsPage { url: NEWS_URL, digest: sha256_hex(&body) })
}

/// Links the panel opens in a new window go to the system browser, and only web links: never
/// file:, javascript: or custom schemes.
fn external_url(url: &Url) -> Option<&str> {
    matches!(url.scheme(), "http" | "https").then(|| url.as_str())
}

#[tauri::command]
async fn launch_game(state: State<'_, AppState>) -> Result<(), String> {
    if state.ownership().is_none() {
        return Err("Wing Commander: Privateer wasn't found.".into());
    }
    state.installer().launch().map(|_| ()).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let config_path = app.path().app_config_dir()?.join("launcher.json");
            let default_install_dir = app.path().local_data_dir()?.join("Privateer Reimagined");
            let config = std::fs::read(&config_path)
                .ok()
                .and_then(|b| serde_json::from_slice(&b).ok())
                .unwrap_or_default();
            app.manage(AppState {
                config: Mutex::new(config),
                config_path,
                default_install_dir,
                release: Mutex::new(None),
                launcher_update: Mutex::new(None),
                busy: AtomicBool::new(false),
            });
            // Built here ("create": false in tauri.conf.json) to attach the new-window hook:
            // nothing the news panel opens ever becomes a window inside the launcher.
            let window = app.config().app.windows.iter().find(|w| w.label == "main").cloned()
                .ok_or("tauri.conf.json has no window labelled main")?;
            let handle = app.handle().clone();
            WebviewWindowBuilder::from_config(app.handle(), &window)?
                .on_new_window(move |url, _features| {
                    if let Some(link) = external_url(&url) {
                        let _ = handle.opener().open_url(link, None::<&str>);
                    }
                    NewWindowResponse::Deny
                })
                .build()?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status,
            set_privateer_folder,
            set_install_dir,
            check_update,
            install,
            launch_game,
            news_page,
            release_hint,
            check_launcher_update,
            install_launcher_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Privateer Reimagined launcher");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn conf() -> serde_json::Value {
        serde_json::from_str(include_str!("../tauri.conf.json")).unwrap()
    }

    #[test]
    fn only_web_links_leave_the_launcher() {
        // The footer's privacy and Discord links (#61, #131) take the same path as the news panel's.
        for ok in ["https://privateer-reimagined.com/#fleet", "https://privateer-reimagined.com/privacy/",
                   "https://discord.gg/9Vhm4tdymY", "http://example.com/"] {
            assert_eq!(external_url(&Url::parse(ok).unwrap()), Some(ok));
        }
        for bad in ["file:///C:/Windows/system32/calc.exe", "javascript:alert(1)", "ms-settings:",
                    "tauri://localhost/", "data:text/html,hi"] {
            assert_eq!(external_url(&Url::parse(bad).unwrap()), None, "{bad}");
        }
    }

    /// The `const X_URL = "..."` links ui/app.js hands to the opener plugin.
    fn ui_urls() -> std::collections::BTreeSet<String> {
        include_str!("../../ui/app.js").lines()
            .filter(|l| l.starts_with("const ") && l.contains("_URL = \""))
            .map(|l| l.split('"').nth(1).unwrap().to_string())
            .collect()
    }

    /// The opener refuses any URL its capability scope doesn't list (#132): an unscoped
    /// `opener:allow-open-url` refuses everything. So the scope names exactly the UI's links.
    #[test]
    fn opener_allows_exactly_the_ui_links() {
        let cap: serde_json::Value = serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        let perm = cap["permissions"].as_array().unwrap().iter()
            .find(|p| p["identifier"] == "opener:allow-open-url")
            .expect("opener:allow-open-url, with its URL scope");
        let allowed: std::collections::BTreeSet<String> = perm["allow"].as_array().unwrap().iter()
            .map(|e| e["url"].as_str().unwrap().to_string())
            .collect();
        let urls = ui_urls();
        assert!(!urls.is_empty(), "app.js's *_URL constants weren't found");
        assert_eq!(allowed, urls);
        for url in &urls {
            assert!(url.starts_with("https://"), "{url}");
            assert!(!url.contains(['*', '?', '[']), "{url}: scope entries are globs; keep them literal");
        }
    }

    #[test]
    fn csp_frames_exactly_the_news_origin() {
        let csp = conf()["app"]["security"]["csp"].as_str().unwrap().to_string();
        let origin = Url::parse(NEWS_URL).unwrap().origin().ascii_serialization();
        let frame_src = csp.split(';').map(str::trim).find(|d| d.starts_with("frame-src")).unwrap();
        assert_eq!(frame_src, format!("frame-src {origin}"));
    }

    #[test]
    fn main_window_is_built_in_code() {
        let windows = conf()["app"]["windows"].as_array().unwrap().clone();
        assert_eq!(windows.len(), 1);
        assert_eq!(windows[0]["label"], "main");
        assert_eq!(windows[0]["create"], false, "else Tauri creates it without the new-window hook");
    }

    #[test]
    fn updater_trusts_a_minisign_key_and_reads_the_cdn_feed() {
        use base64::Engine;
        let updater = &conf()["plugins"]["updater"];
        let key = base64::engine::general_purpose::STANDARD.decode(updater["pubkey"].as_str().unwrap()).unwrap();
        assert!(String::from_utf8(key).unwrap().starts_with("untrusted comment: minisign public key"));
        assert_eq!(updater["endpoints"], serde_json::json!(["https://cdn.privateer-reimagined.com/launcher/latest.json"]));
        assert_eq!(updater["windows"]["installMode"], "passive");
        assert_eq!(conf()["bundle"]["createUpdaterArtifacts"], true, "else no .sig, and launchers refuse the update");
    }

    #[test]
    fn versions_agree() {
        let package: serde_json::Value = serde_json::from_str(include_str!("../../package.json")).unwrap();
        assert_eq!(conf()["version"], env!("CARGO_PKG_VERSION"));
        assert_eq!(package["version"], env!("CARGO_PKG_VERSION"));
    }
}
