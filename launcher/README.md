# Privateer Reimagined launcher (Tauri v2)

A thin UI over [`crates/prl-core`](../crates/prl-core). Every rule (ownership gate,
release-key check, one operation at a time) lives in `src-tauri/src/lib.rs`; `ui/` only
renders state.

![launcher](../docs/images/launcher.png)

```
ui/              index.html + style.css + app.js (no bundler; embedded at build time)
src-tauri/       Rust backend, tauri.conf.json, capabilities, icons
```

## Run it

```bat
cd launcher
npm install
npx tauri dev
```

Debug builds accept two overrides, so you can test against a local store (e.g. one made
with `prl build` + `prl publish --to <folder>`):

| Variable | Effect | In release builds |
|---|---|---|
| `PRL_CDN` | store URL or local folder (default `https://cdn.privateer-reimagined.com`) | honoured (signatures are still checked) |
| `PRL_PUBLIC_KEY` | trust a scratch signing key | **ignored**: only the compiled-in key (`crates/prl-core/src/keys.rs`) is trusted |

## News panel

The left half of the window shows <https://privateer-reimagined.com/launcher/>, which is
`site/launcher/index.html` in this repo. Edit that page and merge, and every launcher shows the
change the next time it starts; no launcher release needed (#44). The buttons and every rule
stay compiled into the launcher.

- The panel is `<iframe sandbox="allow-same-origin allow-popups">`: no scripts, forms or
  top-level navigation, and no access to the launcher's commands. Keep the page script-free.
- Links with `target="_blank"` open in the player's browser (http/https only, via the
  `on_new_window` hook in `lib.rs`); other links navigate inside the panel.
- The site lets only the launcher frame that page (`site/_headers`, `frame-ancestors`), and
  the launcher's CSP frames only the site (`frame-src`, checked by a unit test).
- If the page doesn't answer within 4 s (offline), the panel shows a built-in message.

Settings live in `%APPDATA%\com.privateer-reimagined.launcher\launcher.json` (the
Privateer folder found, and the install folder). The default install folder is
`%LOCALAPPDATA%\Privateer Reimagined`.

## Build the installer

```bat
npx tauri build
```

Produces `target\release\bundle\nsis\Privateer Reimagined_<version>_x64-setup.exe`
(per-user install, no admin prompt). Bump the version in `src-tauri/tauri.conf.json`,
`src-tauri/Cargo.toml` and `package.json` together (a unit test checks they agree). Publish it
with `uv run prl upload-launcher --installer <exe> --version <v> --to r2` (from `publisher/`;
versioned names are immutable) and point the website's download button at it.

**Before the first public release:**

1. `uv run prl keygen` and paste the public key into `crates/prl-core/src/keys.rs`;
   until then the launcher refuses every release.
2. Code-sign the installer, or Windows SmartScreen will warn players.
3. Replace the placeholder icon (`src-tauri/app-icon.png`, then `npx tauri icon`).
