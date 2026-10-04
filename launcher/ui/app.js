// Launcher UI: renders backend state; every rule lives in src-tauri/src/lib.rs.
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { open } = window.__TAURI__.dialog;
const { openUrl } = window.__TAURI__.opener;

const GOG_URL = "https://www.gog.com/en/game/wing_commander_privateer";
const $ = (id) => document.getElementById(id);
const STORE_NAMES = { gog: "GOG", steam: "Steam", folder: "your folder" };

let owned = false;
let update = null;    // last check_update result
let busy = false;

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
  $("verify").disabled = busy || !owned || !update || !update.installed_version;
  $("change-dir").disabled = busy;
  if (!owned) {
    primary.disabled = true;
    primary.textContent = "Privateer required";
    return;
  }
  primary.disabled = busy || !update;
  if (busy) return;
  if (!update) primary.textContent = "Check for updates";
  else if (!update.installed_version) primary.textContent = `Install (${bytes(update.download_bytes)})`;
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
    const s = update.up_to_date
      ? `Version ${update.version}, up to date.`
      : update.installed_version
        ? `Version ${update.version} is available (installed: ${update.installed_version}). ` +
          `${update.files_to_write} file(s) to update, ${bytes(update.download_bytes)} to download.`
        : `Version ${update.version}: ${bytes(update.download_bytes)} to download.`;
    $("update-status").textContent = s;
  } catch (e) {
    update = null;
    $("update-status").textContent = "Couldn't reach the update server.";
    showError(String(e));
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
    $("update-status").textContent = `Version ${version} installed.`;
    await checkUpdate();
  } catch (e) {
    showError(String(e));
    setBusy(false);
  } finally {
    $("progress").hidden = true;
  }
}

async function primaryClicked() {
  if (!update) return checkUpdate();
  if (update.up_to_date) {
    try { await invoke("launch_game"); } catch (e) { showError(String(e)); }
    return;
  }
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

// The news panel (#44): shown only if the site answers now; otherwise the built-in fallback.
async function loadNews() {
  const url = await invoke("news_url");
  if (!url) return;
  const frame = $("news-frame");
  frame.addEventListener("load", () => {
    frame.hidden = false;
    $("news-fallback").hidden = true;
  }, { once: true });
  frame.src = url;
}

async function main() {
  loadNews().catch(() => {});   // never holds up the game controls
  $("primary").addEventListener("click", primaryClicked);
  $("verify").addEventListener("click", verifyClicked);
  $("browse-privateer").addEventListener("click", browsePrivateer);
  $("get-gog").addEventListener("click", () => openUrl(GOG_URL));
  $("change-dir").addEventListener("click", changeInstallDir);
  await listen("progress", ({ payload: p }) => {
    const pct = p.total_bytes ? Math.round((p.done_bytes / p.total_bytes) * 100) : 100;
    $("progress-bar").style.width = (p.phase === "download" ? pct : 100) + "%";
    $("progress-text").textContent = p.phase === "download"
      ? `Downloading ${bytes(p.done_bytes)} of ${bytes(p.total_bytes)} (${p.files_done}/${p.files_total} files)`
      : p.phase === "install" ? `Installing ${p.files_done}/${p.files_total} files` : "Done";
  });

  const status = await invoke("get_status");
  $("install-dir").textContent = status.install_dir;
  $("cdn").textContent = status.cdn;
  renderOwnership(status.owned);
  if (status.owned) await checkUpdate();
}

main().catch((e) => showError(String(e)));
