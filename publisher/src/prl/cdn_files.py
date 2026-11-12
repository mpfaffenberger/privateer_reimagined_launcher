"""Write-once files on the CDN (#27, #34): published names never change their bytes."""

from __future__ import annotations

import contextlib
import hashlib
import re
from pathlib import Path
from typing import BinaryIO

from .store import Store

CDN = "https://cdn.privateer-reimagined.com"
PREFIX = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")
# Suffixes kept whole when the content hash goes into a name (game packages, #1508).
DOUBLE_SUFFIXES = (".tar.gz",)
CHUNK = 1 << 20


def sha256_stream(stream: BinaryIO) -> str:
    """Hash in chunks: game packages are gigabytes."""
    h = hashlib.sha256()
    while chunk := stream.read(CHUNK):
        h.update(chunk)
    return h.hexdigest()


def split_name(name: str) -> tuple[str, str]:
    """(stem, lowercase suffix), keeping `.tar.gz` together."""
    suffix = next((s for s in DOUBLE_SUFFIXES if name.lower().endswith(s)), Path(name).suffix.lower())
    return name[: len(name) - len(suffix)], suffix


def publish_immutable(store: Store, key: str, path: Path, log=print, sha: str | None = None) -> str:
    """Upload `path` as `key`; returns its SHA-256 (`sha`, if the caller already has it).
    Identical bytes already there are a no-op; different bytes under a published key are
    refused (pages link these and print hashes)."""
    if sha is None:
        with open(path, "rb") as f:
            sha = sha256_stream(f)
    if store.exists(key):
        with contextlib.closing(store.open(key)) as published:
            if sha256_stream(published) != sha:
                raise FileExistsError(f"{key} is already published with different contents; "
                                      "publish it under a new name instead")
        log(f"[cdn] {key} already published, identical")
    else:
        store.put_file(key, path)
        log(f"[cdn] uploaded {key} ({path.stat().st_size / 1e6:.2f} MB)")
    return sha


def upload_file(store: Store, path: Path, prefix: str, log=print) -> str:
    """Publish under `prefix/<stem>-<sha10><suffix>`: content-addressed, so a changed file
    always gets a new URL and caches never serve stale bytes. Returns the public URL."""
    prefix = prefix.strip("/")
    if not PREFIX.match(prefix) or ".." in prefix:
        raise ValueError(f"bad prefix {prefix!r} (lowercase letters, digits, / _ - only)")
    with open(path, "rb") as f:
        sha = sha256_stream(f)
    stem, suffix = split_name(path.name)
    key = f"{prefix}/{stem}-{sha[:10]}{suffix}"
    publish_immutable(store, key, path, log, sha=sha)
    log(f"[cdn] {CDN}/{key}")
    return f"{CDN}/{key}"
