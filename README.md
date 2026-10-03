# Privateer Reimagined Launcher

Installs, patches and launches **Privateer Reimagined**, and hosts the website for
[privateer-reimagined.com](https://privateer-reimagined.com).

- **Delta updates:** releases are signed manifests of content-addressed files, so players
  download only what changed.
- **Hosting:** game content on Cloudflare R2 (`cdn.privateer-reimagined.com`, no egress
  fees), site on Cloudflare Pages.
- **Ownership:** the launcher installs only for players who own *Wing Commander: Privateer*
  (GOG or Steam).

| Folder | What |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | Decisions, formats, hosting, Cloudflare setup |
| `publisher/` | `prl`: build, sign and publish releases (Python, uv) |
| `crates/prl-core/` | Launcher engine (Rust) |
| `launcher/` | Tauri v2 desktop app |
| `site/` | Static website (Cloudflare Pages) |
