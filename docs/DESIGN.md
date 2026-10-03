# Privateer Reimagined: launcher, patching, hosting

Status: v1 design (2026-10-03). Owner: Mike. Issues #1-#5.

## Decisions

| Question | Decision |
|---|---|
| Repo | Private (`mpfaffenberger/privateer_reimagined_launcher`); can be made public later. |
| Launcher stack | **Tauri v2** (Rust core + HTML UI): ~10 MB installer, Windows first, macOS possible. |
| Original 1993 assets | **Shipped** with the game, **but** the launcher only installs for players who own *Wing Commander: Privateer* on GOG or Steam (see Ownership). |
| Delta updates | **Yes**: content-addressed, file-level, signed manifests. No patch chains. |
| Hosting | **Cloudflare R2** for game content (`cdn.privateer-reimagined.com`), **Cloudflare Pages** for the site (`privateer-reimagined.com`). |

## Why file-level, content-addressed updates

Measured in the game repo (2026-10-03): `assets/` is **3.76 GB in 18,129 files**. In the last
7 days about **1 GB** of those files changed (~300 commits in 14 days). The churn is whole-file
(re-rendered PNGs, re-encoded WAVs), so byte-level diffs (bsdiff/xdelta) buy little, while
skipping unchanged files saves ~70% per weekly patch.

So every file is stored once by its SHA-256, and a release is just a signed list of
`(path, size, sha256)`:

- **Delta for free:** the launcher downloads only hashes it doesn't have on disk.
- **No patch chains:** any old version, or a broken install, goes straight to the latest.
- **Repair / verify:** re-hash the install and fetch whatever doesn't match.
- **Dedupe across releases:** unchanged files are never re-uploaded or re-stored.
- **Rollback:** old manifests stay in the store; move the channel pointer back.

Easy wins for the game repo (separate issues there): ship only runtime-needed files
(sources like `.blend`/`.obj`/`.3ds`/`.zip`/raw captures/legacy `sprites/` inflate the
install), and transcode the 810 MB of WAV music to Opus/OGG (~10x smaller).

## Components

```
publisher/        Python (uv) CLI `prl`: build + sign + publish releases       (#2)
crates/prl-core/  Rust engine: verify, plan, download/apply, repair, ownership  (#3)
launcher/         Tauri v2 app on prl-core                                     (#4)
site/             Static site for Cloudflare Pages                             (#5)
```

## Store layout (R2 bucket, or any directory for tests)

```
channels/<channel>.json              pointer to the current release of a channel
manifests/<version>.json             the release manifest (exact bytes are signed)
manifests/<version>.json.sig         base64 ed25519 signature over those bytes
blobs/<sha[0:2]>/<sha>.zst           file content, one zstd frame
blobs/<sha[0:2]>/<sha>               file content, raw (already-compressed data)
launcher/                            launcher installers (and later, self-update feed)
```

`<sha>` is the lowercase hex SHA-256 of the **decoded** file content. The encoding is part of
the key, so changing the publisher's compression policy can never make a key ambiguous.

### Channel pointer: `channels/stable.json`

```json
{
  "format": 1,
  "channel": "stable",
  "version": "2026.10.03.1",
  "manifest": "manifests/2026.10.03.1.json",
  "manifest_sha256": "<hex sha256 of the manifest file bytes>"
}
```

### Manifest: `manifests/<version>.json`

```json
{
  "format": 1,
  "product": "privateer-reimagined",
  "version": "2026.10.03.1",
  "channel": "stable",
  "created_utc": "2026-10-03T16:00:00Z",
  "launch": { "windows": "new_privateer.exe" },
  "total_size": 3758096384,
  "files": [
    { "path": "new_privateer.exe", "size": 3355443, "sha256": "...", "enc": "zstd", "stored_size": 1200000 },
    { "path": "assets/music/original/menu.wav", "size": 58615808, "sha256": "...", "enc": "zstd", "stored_size": 51000000 }
  ]
}
```

Rules (both sides enforce them):

- `path` is relative, uses `/`, has no empty, `.` or `..` segments, no drive letter, no
  backslash, and no leading `/`. Paths are unique case-insensitively (Windows).
- `files` is sorted by `path`, so the same inputs always give byte-identical manifests.
- `enc` is `"zstd"` or `"raw"`; `stored_size` is the blob's byte size.

### Launcher verification order

1. Fetch `channels/<ch>.json`.
2. Fetch the manifest it names; its SHA-256 must equal `manifest_sha256`.
3. Fetch `.sig`; the ed25519 signature over the manifest bytes must verify against the
   **public key compiled into the launcher**.
4. `manifest.channel` and `manifest.version` must match the pointer; all paths must pass the
   rules above.
5. Every downloaded blob is decoded and its SHA-256 checked **before** it replaces anything.

A compromised CDN can therefore at most deny service or serve an older *signed* release.
(Rollback protection, i.e. refusing versions older than the installed one, is a follow-up.)

## Install layout

```
<install dir>/                       default: %LOCALAPPDATA%\PrivateerReimagined\game
  new_privateer.exe, assets/...      exactly the manifest's files
  .prl/installed.json                the manifest that is installed
  .prl/hashcache.json                path -> (size, mtime, sha256) so updates skip re-hashing
  .prl/staging/                      in-flight downloads (resumable, never half-applied)
```

Applying a release: download and verify into staging, then atomically rename into place, then
delete files that were in the old manifest but not the new one (**only manifest-owned files**,
so saves and settings are never touched), then write `installed.json`.

## Ownership check

The original assets ship, so the launcher installs only for players who own the original:

1. **Find candidates:**
   - GOG on Windows: `HKLM\SOFTWARE\(WOW6432Node\)GOG.com\Games\*` (`gameName`, `path`).
   - GOG on macOS: `/Applications/Wing Commander*Privateer*.app/Contents/Resources/game`.
   - Steam: `SteamPath` (registry) → `steamapps/libraryfolders.vdf` → each library's
     `appmanifest_*.acf` whose `name` mentions Privateer.
   - Common folders: `C:\GOG Games\*Privateer*`, `C:\Program Files (x86)\GOG Galaxy\Games\*Privateer*`.
   - Or a folder the player picks.
2. **Accept a folder only if it holds real Privateer data**, i.e. a file named `GAME.GOG`,
   `PRIV.EXE`, `PRCD.EXE` or `PRIV.TRE` within a few levels. In the GOG release `PRIV.TRE` sits
   inside the `GAME.GOG` disc image, and `PRIV.EXE` boots `PRCD.EXE`. Matching files rather
   than names also rules out *Privateer 2: The Darkening*.

The verified path is remembered and re-checked at each launch. This is a good-faith gate, not
DRM: it keeps the honest path honest and shows we expect players to own the original.

## Release flow (Mike's machine first; CI later)

```
cd privateer_reimagined_launcher/publisher
uv run prl build   --config ../release.toml --version 2026.10.03.1 --channel stable --out dist/
uv run prl publish --release dist/2026.10.03.1 --store r2       # uploads only new blobs
```

Publishing from a local checkout avoids Git LFS bandwidth charges: a CI build would pull
~3.7 GB of LFS objects per run.

## Hosting and costs

- **R2:** about $0.015/GB-month storage and **no egress fees**. ~20 GB of releases costs about
  $0.30/month. 1,000 players x 3 GB is ~3 TB of downloads: **$0** on R2 vs ~$270 on S3.
  Operations: ~$4.50 per million writes, ~$0.36 per million reads.
- **Pages:** free; deploys `site/` on push.
- **Windows code signing** (a follow-up): unsigned installers trigger SmartScreen. Azure
  Trusted Signing is ~$10/month.

## Cloudflare setup (Mike)

1. **R2:** create bucket `privateer-reimagined`; Settings → Custom Domains →
   `cdn.privateer-reimagined.com` (public read).
2. **R2 API token** (Object Read & Write, this bucket only). Put it in the publisher's
   environment, never in the repo:
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
3. **Pages:** Create project → connect this GitHub repo → framework *None*, build command
   *(empty)*, output directory `site` → custom domain `privateer-reimagined.com`.
4. **Signing key:** `uv run prl keygen` once; keep `signing_key.pem` offline, backed up, and
   **out of git**. Its public key goes in `crates/prl-core/src/keys.rs`.

## Follow-ups (not in v1)

Launcher self-update (Tauri updater feed on R2), Windows code signing, CI for tests,
rollback protection, macOS packaging, chunk-level dedupe if assets ever become big packed
archives, game-repo runtime-asset allowlist and audio transcoding.
