"""Publish the launcher installer to the CDN (#27) and the self-update feed (#48)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .cdn_files import CDN, publish_immutable
from .store import Store

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# Read by every installed launcher's updater (tauri.conf.json plugins.updater.endpoints).
FEED_KEY = "launcher/latest.json"
PLATFORM = "windows-x86_64"


def installer_key(version: str) -> str:
    if not VERSION.match(version):
        raise ValueError(f"launcher version must look like 1.2.3, got {version!r}")
    return f"launcher/PrivateerReimagined-Setup-{version}-x64.exe"


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def feed_version(store: Store) -> str | None:
    """The version the update feed currently offers, if there is a feed."""
    return json.loads(store.get(FEED_KEY))["version"] if store.exists(FEED_KEY) else None


def feed(version: str, signature: str, notes: str, pub_date: datetime) -> dict:
    """Tauri updater 'static JSON' format."""
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platforms": {PLATFORM: {"signature": signature, "url": f"{CDN}/{installer_key(version)}"}},
    }


def upload_installer(store: Store, installer: Path, version: str, notes: str = "", log=print,
                     now: datetime | None = None) -> str:
    """Upload `installer` as `version` with its updater signature, then point the update feed
    at it (last, so the feed never names a file that isn't there yet). Returns its SHA-256.
    Names are versioned rather than content-hashed because the site links them by version;
    bump the version to change bytes."""
    key = installer_key(version)
    if version not in installer.name:
        raise ValueError(f"{installer.name} doesn't look like the {version} installer")
    sig = installer.with_name(installer.name + ".sig")
    if not sig.is_file():
        raise FileNotFoundError(f"{sig.name} is missing: build with TAURI_SIGNING_PRIVATE_KEY set "
                                "(the key in %USERPROFILE%\\.prl) so launchers can verify the update")
    offered = feed_version(store)
    if offered and _version_tuple(offered) > _version_tuple(version):
        raise ValueError(f"the update feed already offers {offered}; refusing to roll launchers back to {version}")
    try:
        sha = publish_immutable(store, key, installer, log)
        publish_immutable(store, key + ".sig", sig, log)
    except FileExistsError as e:
        raise FileExistsError(f"{e}; bump the launcher version") from None
    body = feed(version, sig.read_text(encoding="utf-8").strip(), notes, now or datetime.now(timezone.utc))
    store.put_bytes(FEED_KEY, json.dumps(body, indent=1).encode())
    log(f"[launcher] {CDN}/{key}\n[launcher] sha256 {sha}\n[launcher] update feed {CDN}/{FEED_KEY} -> {version}")
    return sha
