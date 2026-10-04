"""Publish the launcher installer to the CDN under an immutable, versioned name (#27)."""

from __future__ import annotations

import re
from pathlib import Path

from .cdn_files import CDN, publish_immutable
from .store import Store

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def installer_key(version: str) -> str:
    if not VERSION.match(version):
        raise ValueError(f"launcher version must look like 1.2.3, got {version!r}")
    return f"launcher/PrivateerReimagined-Setup-{version}-x64.exe"


def upload_installer(store: Store, installer: Path, version: str, log=print) -> str:
    """Upload `installer` as `version`; returns its SHA-256. The name is versioned rather than
    content-hashed because the site links it by version; bump the version to change bytes."""
    key = installer_key(version)
    if version not in installer.name:
        raise ValueError(f"{installer.name} doesn't look like the {version} installer")
    try:
        sha = publish_immutable(store, key, installer, log)
    except FileExistsError as e:
        raise FileExistsError(f"{e}; bump the launcher version") from None
    log(f"[launcher] {CDN}/{key}\n[launcher] sha256 {sha}")
    return sha
