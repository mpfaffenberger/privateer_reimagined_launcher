"""build: sources -> local store + signed manifest. publish: local store -> any store.

The local store doubles as a cross-release blob cache: only content that has never been
seen before is compressed, and only blobs the destination lacks are uploaded.
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

from . import config, sign
from .manifest import FileEntry, Manifest, blob_key, channel_key, channel_pointer, manifest_key
from .store import LocalStore, Store

CHUNK = 1 << 20
# Store raw when zstd saves less than this fraction (PNG/MP3/OGG are already compressed).
MIN_SAVING = 0.05
# An un-pulled Git LFS file is a tiny text stub that starts like this (#19).
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
LFS_POINTER_MAX = 1024


def lfs_pointers(files: list[tuple[Path, str]]) -> list[str]:
    """Install paths whose source is an LFS pointer instead of real content."""
    found = []
    for src, dest in files:
        if src.stat().st_size <= LFS_POINTER_MAX:
            with open(src, "rb") as f:
                if f.read(len(LFS_POINTER)) == LFS_POINTER:
                    found.append(dest)
    return found


def sha256_file(path: Path) -> tuple[str, int]:
    h, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def existing_blob(store: LocalStore, sha: str) -> tuple[str, int] | None:
    """(enc, stored_size) if the store already has this content in either encoding."""
    for enc in ("zstd", "raw"):
        p = store.local_path(blob_key(sha, enc))
        if p and p.is_file():
            return enc, p.stat().st_size
    return None


def store_blob(store: LocalStore, src: Path, sha: str, size: int, level: int) -> tuple[str, int]:
    """Compress `src` into the store (or store it raw if zstd doesn't pay)."""
    if found := existing_blob(store, sha):
        return found
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


def build(config_path: Path, version: str, channel: str, store: LocalStore, key_path: Path,
          created_utc: str | None = None, workers: int = 8, level: int = 10,
          log=print) -> Manifest:
    cfg = config.load(config_path)
    files = config.collect(cfg)
    log(f"[build] {len(files)} files from {len(cfg.sources)} source(s)")
    if pointers := lfs_pointers(files):
        shown = "\n  ".join(pointers[:20])
        raise ValueError(f"{len(pointers)} file(s) are Git LFS pointers, not content "
                         f"(run `git lfs pull` in the game repo):\n  {shown}")
    store.root.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(workers) as pool:
        hashed = list(pool.map(lambda pair: sha256_file(pair[0]), files))
    log(f"[build] hashed {sum(s for _, s in hashed) / 1e9:.2f} GB")

    # One compression job per unique content; duplicates and cached blobs are free.
    first_src: dict[str, tuple[Path, int]] = {}
    for (src, _), (sha, size) in zip(files, hashed):
        first_src.setdefault(sha, (src, size))
    new = [sha for sha in first_src if not existing_blob(store, sha)]
    log(f"[build] {len(first_src)} unique blobs, {len(new)} new to compress")
    with ThreadPoolExecutor(workers) as pool:
        encoded = dict(zip(first_src, pool.map(
            lambda sha: store_blob(store, *first_src[sha][:1], sha, first_src[sha][1], level),
            first_src)))

    entries = [FileEntry(dest, size, sha, *encoded[sha])
               for (_, dest), (sha, size) in zip(files, hashed)]
    manifest = Manifest(cfg.product, version, channel,
                        created_utc or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        cfg.launch, entries)
    data = manifest.to_bytes()

    mkey = manifest_key(version)
    if store.exists(mkey) and store.get(mkey) != data:
        raise FileExistsError(f"version {version} already exists with different contents; "
                              "versions are immutable, so pick a new one")
    store.put_bytes(mkey, data)
    store.put_bytes(mkey + ".sig", sign.sign(sign.load_private_key(key_path), data))
    log(f"[build] wrote {mkey} ({sum(e.size for e in entries) / 1e9:.2f} GB installed, "
        f"{sum(e.stored_size for e in {e.sha256: e for e in entries}.values()) / 1e9:.2f} GB stored)")
    return manifest


def publish(src: LocalStore, dst: Store, version: str, workers: int = 8, log=print) -> dict:
    """Copy one release to `dst`: missing blobs, then manifest + sig, then the channel pointer
    LAST, so a client never sees a pointer to a release that isn't fully uploaded."""
    mkey = manifest_key(version)
    data = src.get(mkey)
    manifest = Manifest.from_bytes(data)
    needed = {f.blob_key(): f.stored_size for f in manifest.files}
    present = dst.keys("blobs/")
    missing = sorted(k for k in needed if k not in present)
    log(f"[publish] {len(needed)} blobs referenced, {len(missing)} to upload "
        f"({sum(needed[k] for k in missing) / 1e9:.2f} GB)")
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda k: dst.put_file(k, src.local_path(k)), missing))
    dst.put_bytes(mkey, data)
    dst.put_bytes(mkey + ".sig", src.get(mkey + ".sig"))
    dst.put_bytes(channel_key(manifest.channel), channel_pointer(manifest.channel, version, data))
    log(f"[publish] {manifest.channel} -> {version}")
    return {"uploaded": len(missing), "referenced": len(needed),
            "uploaded_bytes": sum(needed[k] for k in missing)}


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
    present = store.keys("blobs/")
    for f in manifest.files:
        if f.blob_key() not in present:
            problems.append(f"missing blob for {f.path}")
        elif deep:
            raw = store.get(f.blob_key())
            body = zstandard.ZstdDecompressor().decompressobj().decompress(raw) if f.enc == "zstd" else raw
            if hashlib.sha256(body).hexdigest() != f.sha256:
                problems.append(f"blob content mismatch for {f.path}")
    log(f"[verify] {channel} -> {manifest.version}: {len(manifest.files)} files, "
        f"{'OK' if not problems else str(len(problems)) + ' problem(s)'}")
    return problems
