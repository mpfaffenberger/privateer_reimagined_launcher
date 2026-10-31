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

From Mike's machine (full checkout, all LFS content present):

```bat
set GAME_REPO=C:\Users\mpfaffenberger\code\new_privateer
uv run prl build   --config ..\release.toml --version 2026.10.03.1
uv run prl publish --version 2026.10.03.1 --to r2
uv run prl verify  --store r2 --channel stable --pubkey <base64 public key>
```

The signing key defaults to `%USERPROFILE%\.prl\signing_key.pem` (`--key` to override).

## Cutting a release from CI (#21)

CI checks out **without LFS**, so assets are pointer stubs. A pointer's `oid` is exactly
the manifest SHA-256, so CI describes the release without downloading anything, reuses
every blob R2 already has, and `git lfs pull`s **only** content R2 lacks:

```bat
rem CI (R2 credentials, NO signing key):
uv run prl build   --config ..\release.toml --version 2026.10.04.1 --lfs-pointers --known-store r2 --unsigned
uv run prl publish --version 2026.10.04.1 --to r2 --blobs-only
rem -> artifact: dist\store\manifests\2026.10.04.1.json (not live yet)

rem Mike's machine (signing key). The game repo's release workflow prints the run id:
uv run prl sign-publish --run <run id> --to r2 --dry-run   & rem every check, nothing goes live
uv run prl sign-publish --run <run id> --to r2             & rem live
```

`--run` fetches the run's `release-manifest` artifact with `gh`; `--manifest FILE` takes a
local file instead.

- `sign-publish` refuses unless **every** referenced blob is already in R2 with the right
  size, then uploads manifest + signature and moves the channel pointer last.
- Every pulled file is re-hashed against its pointer before use.
- Paths `git lfs pull --include` can't take literally (`,` `*` `?` `[` `]` `!` `#`) are
  refused rather than guessed at. The game repo has none today.

- `build` hashes everything, but compresses **only content never seen before** into the
  local store (`dist/store`, which doubles as a cache across releases). Already-compressed
  files (PNG, MP3) are stored raw.
- `publish` uploads only blobs R2 doesn't have, then the manifest and signature, and moves
  `channels/<channel>.json` **last**, so players never see a half-uploaded release. Just
  before the pointer, it adds the release's game executable to `stats/markers.json`, which
  the site counts downloads by ([`docs/DOWNLOAD_COUNTS.md`](../docs/DOWNLOAD_COUNTS.md)).
- Versions are immutable: rebuilding one with different contents is refused.
- `--to` / `--store` also accept a local folder, handy for dry runs and launcher testing.

## Tests

```bat
uv run pytest
```
