"""Write-once files on the CDN (#27, #34): published names never change their bytes."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .store import Store

CDN = "https://cdn.privateer-reimagined.com"
PREFIX = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")


def publish_immutable(store: Store, key: str, path: Path, log=print) -> str:
    """Upload `path` as `key`; returns its SHA-256. Identical bytes already there are a no-op;
    different bytes under a published key are refused (pages link these and print hashes)."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if store.exists(key):
        if hashlib.sha256(store.get(key)).hexdigest() != sha:
            raise FileExistsError(f"{key} is already published with different contents; "
                                  "publish it under a new name instead")
        log(f"[cdn] {key} already published, identical")
    else:
        store.put_file(key, path)
        log(f"[cdn] uploaded {key} ({len(data) / 1e6:.2f} MB)")
    return sha


def upload_file(store: Store, path: Path, prefix: str, log=print) -> str:
    """Publish under `prefix/<stem>-<sha10><suffix>`: content-addressed, so a changed file
    always gets a new URL and caches never serve stale bytes. Returns the public URL."""
    prefix = prefix.strip("/")
    if not PREFIX.match(prefix) or ".." in prefix:
        raise ValueError(f"bad prefix {prefix!r} (lowercase letters, digits, / _ - only)")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    key = f"{prefix}/{path.stem}-{sha[:10]}{path.suffix.lower()}"
    publish_immutable(store, key, path, log)
    log(f"[cdn] {CDN}/{key}")
    return f"{CDN}/{key}"
