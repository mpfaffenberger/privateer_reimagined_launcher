// Launcher UI: renders backend state; every rule lives in src-tauri/src/lib.rs.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { open } = window.__TAURI__.dialog;
const { openUrl } = window.__TAURI__.opener;

const GOG_URL = "https://www.gog.com/en/game/wing_commander_privateer";
const PRIVACY_URL = "https://privateer-reimagined.com/privacy/";   // #61
const $ = (id) => document.getElementById(id);
const STORE_NAMES = { gog: "GOG", steam: "Steam", folder: "your folder" };

let owned = false;
let update = null;    // last check_update result
let installedVersion = null;   // from get_status: known without the network
let checkFailed = false;       // the last update check couldn't reach the server (#45)
let busy = false;

// Offline with a game installed: play what's there rather than block on the update server.
const canPlayOffline = () => !update && checkFailed && !!installedVersion;
// The server answered, but nothing is published for this computer yet (#66): no fault, no retry.
const nothingPublished = () => !!update && !update.published;

function bytes(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
  if (n >= 1e3) return (n / 1e3).toFixed(0) + " KB";
  return n + " B";
}

function showError(msg) {
  $("error").hidden = !msg;
  $("error").textContent = msg || "";
}

function setBusy(on) {
  busy = on;
  render();
}

function render() {
  const primary = $("primary");
  $("verify").disabled = busy || !owned || !update || !update.installed_version || nothingPublished();
  $("change-dir").disabled = busy;
  $("launcher-update").disabled = busy;
  if (!owned) {
    primary.disabled = true;
    primary.textContent = "Privateer required";
    return;
  }
  primary.disabled = busy;
  $("retry").hidden = busy || !checkFailed;
  if (busy) return;
  if (!update) primary.textContent = canPlayOffline() ? "Play" : "Check for updates";
  else if (nothingPublished()) {
    primary.textContent = update.installed_version ? "Play" : "Not available yet";
    primary.disabled = !update.installed_version;
  } else if (!update.installed_version) primary.textContent = `Install (${bytes(update.download_bytes)})`;
  else if (!update.up_to_date) primary.textContent = `Update (${bytes(update.download_bytes)})`;
  else primary.textContent = "Play";
}

function renderOwnership(found) {
  owned = !!found;
  $("owner-actions").hidden = owned;
  if (found) {
    $("owner-status").innerHTML = "";
    const ok = document.createElement("span");
    ok.className = "good";
    ok.textContent = `Found via ${STORE_NAMES[found.store] || found.store}: `;
    $("owner-status").append(ok, document.createTextNode(found.path));
  } else {
    $("owner-status").textContent =
      "We couldn't find your copy. Privateer Reimagined needs Wing Commander: Privateer " +
      "from GOG or Steam. If it's installed somewhere unusual, choose its folder.";
  }
  render();
}

async function checkUpdate(repair = false) {
  showError(null);
  setBusy(true);
  $("primary").textContent = repair ? "Verifying files…" : "Checking…";
  try {
    update = await invoke("check_update", { repair });
    checkFailed = false;
    $("check-details").hidden = true;
    const s = !update.published
      ? (update.installed_version
          ? `Version ${update.installed_version} installed. No newer release for this computer yet.`
          : "There's no release for this computer yet. Check back soon.")
      : update.up_to_date
      ? `Version ${update.version}, up to date.`
      : update.installed_version
        ? `Version ${update.version} is available (installed: ${update.installed_version}). ` +
          `${update.files_to_write} file(s) to update, ${bytes(update.download_bytes)} to download.`
        : `Version ${update.version}: ${bytes(update.download_bytes)} to download.`;
    $("update-status").textContent = s;
  } catch (e) {
    update = null;
    checkFailed = true;
    $("update-status").textContent = installedVersion
      ? `Couldn't check for updates. You can still play version ${installedVersion}.`
      : "Couldn't reach the update server. Check your connection and try again.";
    $("check-detail").textContent = String(e);     // the transport detail, folded away
    $("check-details").hidden = false;
  } finally {
    setBusy(false);
  }
}

async function installOrUpdate(repair = false) {
  showError(null);
  setBusy(true);
  $("primary").textContent = repair ? "Repairing…" : "Installing…";
  $("progress").hidden = false;
  try {
    const version = await invoke("install", { repair });
    installedVersion = version;
    $("update-status").textContent = `Version ${version} installed.`;
    await checkUpdate();
  } catch (e) {
    showError(String(e));
    setBusy(false);
  } finally {
    $("progress").hidden = true;
  }
}

async function play() {
  try { await invoke("launch_game"); } catch (e) { showError(String(e)); }
}

async function primaryClicked() {
  if (canPlayOffline()) return play();
  if (!update) return checkUpdate();
  if (nothingPublished()) return update.installed_version ? play() : undefined;
  if (update.up_to_date) return play();
  return installOrUpdate();
}

async function verifyClicked() {
  await checkUpdate(true);
  if (update && !update.up_to_date) await installOrUpdate(true);
}

async function browsePrivateer() {
  const dir = await open({ directory: true, title: "Choose your Wing Commander: Privateer folder" });
  if (!dir) return;
  try {
    renderOwnership(await invoke("set_privateer_folder", { path: dir }));
    showError(null);
    if (!update) await checkUpdate();
  } catch (e) {
    showError(String(e));
  }
}

async function changeInstallDir() {
  const dir = await open({ directory: true, title: "Where should Privateer Reimagined be installed?" });
  if (!dir) return;
  try {
    $("install-dir").textContent = await invoke("set_install_dir", { path: dir });
    update = null;
    await checkUpdate();
  } catch (e) {
    showError(String(e));
  }
}

// Launcher self-update (#48): offered, never forced; the backend refuses it during a game install.
// A launcher left open re-checks when the player comes back to it, at most hourly (#57).
const LAUNCHER_RECHECK_MS = 60 * 60 * 1000;
let lastLauncherCheck = 0;   // time of the last attempt, failed or not: offline mustn't retry per focus

function launcherRecheckDue() {
  return !busy && Date.now() - lastLauncherCheck >= LAUNCHER_RECHECK_MS;
}

async function checkLauncherUpdate() {
  lastLauncherCheck = Date.now();
  const u = await invoke("check_launcher_update");
  if (!u) return;
  $("launcher-update-text").textContent = `Launcher ${u.version} is ready to install.`;
  $("launcher-update-notes").textContent = u.notes || "";
  $("launcher-update-notes").hidden = !u.notes;
  $("launcher-update-card").hidden = false;
}

async function updateLauncher() {
  showError(null);
  setBusy(true);
  $("launcher-update").textContent = "Updating…";
  $("launcher-update-progress").hidden = false;
  try {
    await invoke("install_launcher_update");   // on success the launcher restarts as the new version
  } catch (e) {
    $("launcher-update").textContent = "Update";
    $("launcher-update-progress").hidden = true;
    showError(`The launcher update failed: ${e}`);
    setBusy(false);
  }
}

// The news panel (#44): shown only if the site answers; otherwise the built-in fallback. Reloaded
// only when the page changed (#78), so a launcher left open picks up news without a flash.
let newsDigest = null;   // the page the panel shows

async function loadNews() {
  const page = await invoke("news_page");
  if (!page || page.digest === newsDigest) return;   // offline: keep the news (or fallback) shown
  const frame = $("news-frame");
  frame.addEventListener("load", () => {
    newsDigest = page.digest;
    frame.hidden = false;
    $("news-fallback").hidden = true;
  }, { once: true });
  frame.src = page.url;   // the site serves it max-age=0, must-revalidate: always fresh
}

// A launcher left open (#78) re-checks game releases and news when the player comes back to the
// window and on a timer (a window left in front never gets a focus event). Both probes are cheap:
// the channel pointer and the news page. The full verified check runs only when the pointer moved.
const RECHECK_MS = 10 * 60 * 1000;
const RECHECK_TICK_MS = 60 * 1000;   // a no-op unless a check is due: focus and timer never stack
let lastRecheck = 0;
let releaseHint;   // the pointer behind the current `update` (undefined: not probed yet)

async function recheckGame() {
  if (busy || !owned) return;
  const hint = await invoke("release_hint");   // offline: throws, and the last result stands
  if (busy || (update && hint === releaseHint)) return;   // an install started, or nothing new
  releaseHint = hint;
  await checkUpdate();
}

function recheck() {
  if (launcherRecheckDue()) checkLauncherUpdate().catch(() => {});
  if (Date.now() - lastRecheck < RECHECK_MS) return;
  lastRecheck = Date.now();
  loadNews().catch(() => {});
  recheckGame().catch(() => {});
}

async function main() {
  lastRecheck = Date.now();               // the start-up checks below count as the first round
  loadNews().catch(() => {});             // never holds up the game controls
  checkLauncherUpdate().catch(() => {});  // offline: simply no offer
  // The native window's focus event: in WebView2 the DOM's focus/visibilitychange don't fire
  // when the player comes back to the window (checked on a real minimize + restore).
  await listen("tauri://focus", recheck);
  setInterval(recheck, RECHECK_TICK_MS);
  $("launcher-update").addEventListener("click", updateLauncher);
  await listen("launcher-progress", ({ payload: p }) => {
    $("launcher-update-progress").textContent = p.total
      ? `Downloading ${bytes(p.done)} of ${bytes(p.total)}`
      : `Downloading ${bytes(p.done)}`;
  });
  $("primary").addEventListener("click", primaryClicked);
  $("retry").addEventListener("click", () => checkUpdate());
  $("verify").addEventListener("click", verifyClicked);
  $("browse-privateer").addEventListener("click", browsePrivateer);
  $("get-gog").addEventListener("click", () => openUrl(GOG_URL));
  $("privacy").addEventListener("click", () => openUrl(PRIVACY_URL));
  $("change-dir").addEventListener("click", changeInstallDir);
  await listen("progress", ({ payload: p }) => {
    const pct = p.total_bytes ? Math.round((p.done_bytes / p.total_bytes) * 100) : 100;
    $("progress-bar").style.width = (p.phase === "download" ? pct : 100) + "%";
    $("progress-text").textContent = p.phase === "download"
      ? `Downloading ${bytes(p.done_bytes)} of ${bytes(p.total_bytes)} (${p.files_done}/${p.files_total} files)`
      : p.phase === "install" ? `Installing ${p.files_done}/${p.files_total} files` : "Done";
  });

  const status = await invoke("get_status");
  installedVersion = status.installed_version;
  $("install-dir").textContent = status.install_dir;
  $("footer").title = `Update store: ${status.cdn}`;   // for testers (PRL_CDN); players don't need it
  renderOwnership(status.owned);
  if (status.owned) {
    // Probe first: if the pointer moves before the check below, the next probe differs and
    // re-checks. The other way round, a release landing in between would go unnoticed.
    releaseHint = await invoke("release_hint").catch(() => undefined);
    await checkUpdate();
  }
}

main().catch((e) => showError(String(e)));
