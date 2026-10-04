"""Publish the launcher installer to the CDN under an immutable, versioned name (#27)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .store import Store

CDN = "https://cdn.privateer-reimagined.com"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def installer_key(version: str) -> str:
    if not VERSION.match(version):
        raise ValueError(f"launcher version must look like 1.2.3, got {version!r}")
    return f"launcher/PrivateerReimagined-Setup-{version}-x64.exe"


def upload_installer(store: Store, installer: Path, version: str, log=print) -> str:
    """Upload `installer` as `version`; returns its SHA-256. Re-uploading identical bytes is a
    no-op; different bytes under a published name are refused (links and hashes on the site
    must never silently change)."""
    key = installer_key(version)
    if version not in installer.name:
        raise ValueError(f"{installer.name} doesn't look like the {version} installer")
    data = installer.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if store.exists(key):
        if hashlib.sha256(store.get(key)).hexdigest() != sha:
            raise FileExistsError(f"{key} is already published with different contents; "
                                  "bump the launcher version instead")
        log(f"[launcher] {key} already published, identical")
    else:
        store.put_file(key, installer)
        log(f"[launcher] uploaded {key} ({len(data) / 1e6:.2f} MB)")
    log(f"[launcher] {CDN}/{key}\n[launcher] sha256 {sha}")
    return sha
