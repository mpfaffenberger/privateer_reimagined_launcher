"""build: sources -> local store + manifest. publish / sign-publish: -> any store.

The local store doubles as a cross-release blob cache: only content that has never been
seen before is compressed, and only blobs the destination lacks are uploaded.

CI mode (#21): sources may be Git LFS pointers (their oid IS our sha256), blobs the
destination already has are reused without touching the content, and only the rest is
`git lfs pull`ed. The manifest can be left unsigned for `sign-publish` on Mike's machine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import zstandard

from . import config, gitrepo, lfs, sign
from .manifest import FileEntry, Manifest, blob_key, channel_key, channel_pointer, manifest_key
from .store import LocalStore, Store

CHUNK = 1 << 20
# Store raw when zstd saves less than this fraction (PNG/MP3/OGG are already compressed).
MIN_SAVING = 0.05


def sha256_file(path: Path) -> tuple[str, int]:
    h, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def blob_index(store: Store) -> dict[str, tuple[str, int]]:
    """sha256 -> (enc, stored_size) for every blob in `store`, from one listing."""
    index: dict[str, tuple[str, int]] = {}
    for key, size in store.sizes("blobs/").items():
        name = key.rsplit("/", 1)[-1]
        sha, enc = (name[:-4], "zstd") if name.endswith(".zst") else (name, "raw")
        if len(sha) == 64 and (sha not in index or enc == "zstd"):
            index[sha] = (enc, size)
    return index


def describe(files: list[tuple[Path, str]], allow_pointers: bool,
             workers: int) -> list[tuple[str, int, bool]]:
    """(sha256, size, is_pointer) per file: read from the LFS pointer when allowed (no
    download), otherwise by hashing the content."""
    def one(src: Path) -> tuple[str, int, bool]:
        if allow_pointers and (ptr := lfs.read_pointer(src)):
            return (*ptr, True)
        return (*sha256_file(src), False)
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda pair: one(pair[0]), files))


def store_blob(store: LocalStore, src: Path, sha: str, size: int, level: int) -> tuple[str, int]:
    """Compress `src` into the store (or store it raw if zstd doesn't pay)."""
    with tempfile.NamedTemporaryFile(dir=store.root, delete=False, suffix=".tmp") as tmp:
        tmp_path = Path(tmp.name)
        with open(src, "rb") as fin:
            zstandard.ZstdCompressor(level=level).copy_stream(fin, tmp)
    try:
        zsize = tmp_path.stat().st_size
        if size > 0 and zsize <= size * (1.0 - MIN_SAVING):
            dest = store.local_path(blob_key(sha, "zstd"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, dest)
            return "zstd", zsize
        store.put_file(blob_key(sha, "raw"), src)
        return "raw", size
    finally:
        tmp_path.unlink(missing_ok=True)


def build(config_path: Path, version: str, channel: str, store: LocalStore, key_path: Path | None,
          created_utc: str | None = None, workers: int = 8, level: int = 10,
          known: Store | None = None, lfs_pointers: bool = False, pull=lfs.pull,
          os_name: str = "windows", log=print) -> Manifest:
    """Build `version` into `store`.

    known:        a destination (e.g. R2) whose existing blobs count as already built.
    lfs_pointers: accept LFS pointer stubs; pull only content nobody has yet.
    key_path:     None -> unsigned manifest, for `sign_publish` elsewhere.
    os_name:      which OS this release is for; its channel must match (#66).
    """
    config.check_channel_for_os(channel, os_name)
    cfg = config.load(config_path, os_name)
    files = config.collect(cfg)
    if cfg.launch[os_name] not in {dest for _, dest in files}:
        raise ValueError(f"the {os_name} launch target {cfg.launch[os_name]!r} isn't in the release")
    log(f"[build] {len(files)} files from {len(cfg.sources)} source(s)")
    if not lfs_pointers and (pointers := lfs.find_pointers(files)):
        shown = "\n  ".join(pointers[:20])
        raise ValueError(f"{len(pointers)} file(s) are Git LFS pointers, not content "
                         f"(run `git lfs pull` in the game repo):\n  {shown}")
    if stubs := gitrepo.symlink_stubs(files):
        shown = "\n  ".join(stubs[:20])
        raise ValueError(f"{len(stubs)} file(s) are git symlinks checked out as plain text "
                         f"(core.symlinks=false); exclude them or check out with symlinks "
                         f"enabled:\n  {shown}")
    store.root.mkdir(parents=True, exist_ok=True)

    described = describe(files, lfs_pointers, workers)
    n_ptr = sum(p for _, _, p in described)
    log(f"[build] described {sum(s for _, s, _ in described) / 1e9:.2f} GB "
        f"({n_ptr} from LFS pointers, {len(described) - n_ptr} hashed)")

    # One job per unique content. The destination's copy wins, so it needn't be re-uploaded.
    first_src: dict[str, tuple[Path, int, bool]] = {}
    for (src, _), (sha, size, ptr) in zip(files, described):
        first_src.setdefault(sha, (src, size, ptr))
    index = blob_index(store)
    if known is not None:
        index.update(blob_index(known))
    missing = [sha for sha in first_src if sha not in index]
    log(f"[build] {len(first_src)} unique blobs, {len(missing)} new to compress")

    if to_pull := [first_src[sha][0] for sha in missing if first_src[sha][2]]:
        pull(to_pull, log=log)
        for sha in missing:
            src, size, ptr = first_src[sha]
            if ptr and sha256_file(src) != (sha, size):
                raise ValueError(f"{src}: content after `git lfs pull` doesn't match its pointer")
    with ThreadPoolExecutor(workers) as pool:
        index.update(zip(missing, pool.map(
            lambda sha: store_blob(store, first_src[sha][0], sha, first_src[sha][1], level), missing)))

    entries = [FileEntry(dest, size, sha, *index[sha])
               for (_, dest), (sha, size, _) in zip(files, described)]
    manifest = Manifest(cfg.product, version, channel,
                        created_utc or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        cfg.launch, entries)
    data = manifest.to_bytes()

    mkey = manifest_key(version)
    for where in (store, known):
        if where is not None and where.exists(mkey) and where.get(mkey) != data:
            raise FileExistsError(f"version {version} already exists with different contents; "
                                  "versions are immutable, so pick a new one")
    store.put_bytes(mkey, data)
    if key_path is not None:
        store.put_bytes(mkey + ".sig", sign.sign(sign.load_private_key(key_path), data))
    log(f"[build] wrote {mkey}{'' if key_path else ' (UNSIGNED)'} "
        f"({sum(e.size for e in entries) / 1e9:.2f} GB installed, "
        f"{sum(e.stored_size for e in {e.sha256: e for e in entries}.values()) / 1e9:.2f} GB stored)")
    return manifest


def upload_missing_blobs(src: LocalStore, dst: Store, manifest: Manifest, workers: int = 8,
                         log=print) -> dict:
    """Upload the blobs `manifest` needs that `dst` doesn't have yet."""
    needed = {f.blob_key(): f.stored_size for f in manifest.files}
    present = dst.keys("blobs/")
    missing = sorted(k for k in needed if k not in present)
    unavailable = [k for k in missing if not ((p := src.local_path(k)) and p.is_file())]
    if unavailable:
        raise FileNotFoundError(f"{len(unavailable)} blob(s) are in neither the build store nor "
                                f"the destination, e.g. {unavailable[0]}")
    log(f"[publish] {len(needed)} blobs referenced, {len(missing)} to upload "
        f"({sum(needed[k] for k in missing) / 1e9:.2f} GB)")
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda k: dst.put_file(k, src.local_path(k)), missing))
    return {"uploaded": len(missing), "referenced": len(needed),
            "uploaded_bytes": sum(needed[k] for k in missing)}


def check_not_taken(dst: Store, data: bytes) -> Manifest:
    """Refuse if this version is already published with different contents."""
    manifest = Manifest.from_bytes(data)
    mkey = manifest_key(manifest.version)
    if dst.exists(mkey) and dst.get(mkey) != data:
        raise FileExistsError(f"version {manifest.version} is already published with different "
                              "contents; versions are immutable")
    return manifest


def finalize(dst: Store, data: bytes, signature: bytes, log=print) -> None:
    """Manifest + sig, then the channel pointer LAST, so a client never sees a pointer to
    a release that isn't fully uploaded. Refuses to overwrite a different published version."""
    manifest = check_not_taken(dst, data)
    mkey = manifest_key(manifest.version)
    dst.put_bytes(mkey, data)
    dst.put_bytes(mkey + ".sig", signature)
    dst.put_bytes(channel_key(manifest.channel),
                  channel_pointer(manifest.channel, manifest.version, data))
    log(f"[publish] {manifest.channel} -> {manifest.version}")


def publish(src: LocalStore, dst: Store, version: str, workers: int = 8,
            blobs_only: bool = False, log=print) -> dict:
    """Copy one built release to `dst`. `blobs_only` (CI): upload content and stop; the
    manifest is signed and published later by `sign_publish`."""
    mkey = manifest_key(version)
    data = src.get(mkey)
    stats = upload_missing_blobs(src, dst, Manifest.from_bytes(data), workers, log)
    if blobs_only:
        log(f"[publish] blobs only: {version} is NOT live until `prl sign-publish`")
    else:
        finalize(dst, data, src.get(mkey + ".sig"), log)
    return stats


def sign_publish(data: bytes, dst: Store, key_path: Path, dry_run: bool = False,
                 log=print) -> None:
    """Sign an (unsigned) manifest and make it live, but only if every blob it references
    is already in `dst` with the right size. Runs where the signing key lives.
    `dry_run`: every check (blobs, key, version not taken), then stop before publishing."""
    manifest = check_not_taken(dst, data)
    present = dst.sizes("blobs/")
    bad = sorted({f.path for f in manifest.files if present.get(f.blob_key()) != f.stored_size})
    if bad:
        raise ValueError(f"{len(bad)} file(s) have no matching blob in the destination "
                         f"(upload blobs first), e.g. {bad[0]}")
    log(f"[sign-publish] {manifest.version}: all {len({f.blob_key() for f in manifest.files})} "
        f"blobs present, {len(manifest.files)} files, "
        f"{sum(f.size for f in manifest.files) / 1e9:.2f} GB installed")
    signature = sign.sign(sign.load_private_key(key_path), data)
    if dry_run:
        log(f"[sign-publish] dry run OK: signed, NOT published ({manifest.channel} unchanged)")
        return
    finalize(dst, data, signature, log)


def verify(store: Store, channel: str, public_b64: str, deep: bool = False, log=print) -> list[str]:
    """Check a published channel the way the launcher will. Returns a list of problems."""
    import json

    problems: list[str] = []
    pointer = json.loads(store.get(channel_key(channel)))
    data = store.get(pointer["manifest"])
    if hashlib.sha256(data).hexdigest() != pointer["manifest_sha256"]:
        problems.append("manifest sha256 does not match the channel pointer")
    if not sign.verify(public_b64, data, store.get(pointer["manifest"] + ".sig")):
        problems.append("manifest signature does NOT verify against the public key")
    manifest = Manifest.from_bytes(data)
    if (manifest.channel, manifest.version) != (channel, pointer["version"]):
        problems.append("manifest channel/version disagree with the pointer")
    present = store.sizes("blobs/")
    for f in manifest.files:
        if f.blob_key() not in present:
            problems.append(f"missing blob for {f.path}")
        elif present[f.blob_key()] != f.stored_size:
            problems.append(f"blob for {f.path} has the wrong size")
        elif deep:
            raw = store.get(f.blob_key())
            body = zstandard.ZstdDecompressor().decompressobj().decompress(raw) if f.enc == "zstd" else raw
            if hashlib.sha256(body).hexdigest() != f.sha256:
                problems.append(f"blob content mismatch for {f.path}")
    log(f"[verify] {channel} -> {manifest.version}: {len(manifest.files)} files, "
        f"{'OK' if not problems else str(len(problems)) + ' problem(s)'}")
    return problems
