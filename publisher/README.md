# prl: release publisher

Builds signed, content-addressed releases and uploads only what changed.
Formats and guarantees: [`docs/DESIGN.md`](../docs/DESIGN.md).

## One-time setup

```bat
cd publisher
uv run prl keygen --out %USERPROFILE%\.prl\signing_key.pem
```

Keep that key **offline and backed up**; losing it means shipping a launcher with a new
public key. Paste the printed public key into `crates/prl-core/src/keys.rs`.

For R2, set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_BUCKET`
in your environment (never in the repo).

## Cutting a release

```bat
set GAME_REPO=C:\Users\mpfaffenberger\code\new_privateer
uv run prl build   --config ..\release.toml --version 2026.10.03.1 --key %USERPROFILE%\.prl\signing_key.pem
uv run prl publish --version 2026.10.03.1 --to r2
uv run prl verify  --store r2 --channel stable --pubkey <base64 public key>
```

- `build` hashes everything, but compresses **only content never seen before** into the
  local store (`dist/store`, which doubles as a cache across releases). Already-compressed
  files (PNG, MP3) are stored raw.
- `publish` uploads only blobs R2 doesn't have, then the manifest and signature, and moves
  `channels/<channel>.json` **last**, so players never see a half-uploaded release.
- Versions are immutable: rebuilding one with different contents is refused.
- `--to` / `--store` also accept a local folder, handy for dry runs and launcher testing.

## Tests

```bat
uv run pytest
```
