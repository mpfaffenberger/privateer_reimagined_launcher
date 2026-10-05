"""Publish the launcher to the CDN (#27) and the self-update feed (#48), per platform (#69).

Each platform has a file people download from the site and a file the updater fetches
(with its minisign signature beside it). On Windows they're the same NSIS installer; on a
Mac people get a .dmg and the updater fetches the .app.tar.gz. Every name is versioned and
immutable. One feed (launcher/latest.json) lists every platform for ONE version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .cdn_files import CDN, publish_immutable
from .store import Store

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
# Read by every installed launcher's updater (tauri.conf.json plugins.updater.endpoints).
FEED_KEY = "launcher/latest.json"


@dataclass(frozen=True)
class Platform:
    feed_name: str      # Tauri's target name in the feed
    download: str       # site download key; {v} = version
    update: str         # what the updater fetches (+ .sig); may equal `download`


WINDOWS = Platform("windows-x86_64",
                   "launcher/PrivateerReimagined-Setup-{v}-x64.exe",
                   "launcher/PrivateerReimagined-Setup-{v}-x64.exe")
MACOS = Platform("darwin-aarch64",
                 "launcher/PrivateerReimagined-{v}-macos-arm64.dmg",
                 "launcher/PrivateerReimagined-{v}-macos-arm64.app.tar.gz")


def _check_version(version: str) -> str:
    if not VERSION.match(version):
        raise ValueError(f"launcher version must look like 1.2.3, got {version!r}")
    return version


def installer_key(version: str) -> str:
    """The Windows installer's key (what the site's Windows button links to)."""
    return WINDOWS.download.format(v=_check_version(version))


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _read_feed(store: Store) -> dict | None:
    return json.loads(store.get(FEED_KEY)) if store.exists(FEED_KEY) else None


def feed_version(store: Store) -> str | None:
    """The version the update feed currently offers, if there is a feed."""
    current = _read_feed(store)
    return current["version"] if current else None


def feed(version: str, platforms: dict, notes: str, pub_date: datetime) -> dict:
    """Tauri updater 'static JSON' format."""
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }


def publish(store: Store, platform: Platform, version: str, update_file: Path,
            download_file: Path | None = None, notes: str = "", log=print,
            now: datetime | None = None) -> dict[str, str]:
    """Upload one platform's launcher as `version`, then point the feed at it (last, so the
    feed never names a file that isn't there). Returns {key: sha256} of what was uploaded.

    The feed lists one version: publishing a platform at the feed's version adds it beside
    the others; a newer version starts the feed afresh (the other platforms' entries are an
    older build and must not be offered under the new number until they're published too).
    """
    _check_version(version)
    download_file = download_file or update_file
    for f in {update_file, download_file}:
        if version not in f.name:
            raise ValueError(f"{f.name} doesn't look like the {version} launcher")
    sig = update_file.with_name(update_file.name + ".sig")
    if not sig.is_file():
        raise FileNotFoundError(f"{sig.name} is missing: sign it with the updater key in %USERPROFILE%\\.prl "
                                "(tauri build with TAURI_SIGNING_PRIVATE_KEY, or tauri signer sign)")
    current = _read_feed(store)
    if current and _version_tuple(current["version"]) > _version_tuple(version):
        raise ValueError(f"the update feed already offers {current['version']}; "
                         f"refusing to roll launchers back to {version}")

    uploads = {platform.download.format(v=version): download_file,
               platform.update.format(v=version): update_file}
    shas: dict[str, str] = {}
    try:
        for key, path in uploads.items():
            shas[key] = publish_immutable(store, key, path, log)
        update_key = platform.update.format(v=version)
        publish_immutable(store, update_key + ".sig", sig, log)
    except FileExistsError as e:
        raise FileExistsError(f"{e}; bump the launcher version") from None

    platforms = dict(current["platforms"]) if current and current["version"] == version else {}
    platforms[platform.feed_name] = {"signature": sig.read_text(encoding="utf-8").strip(),
                                     "url": f"{CDN}/{update_key}"}
    body = feed(version, platforms, notes, now or datetime.now(timezone.utc))
    store.put_bytes(FEED_KEY, json.dumps(body, indent=1).encode())
    for key, sha in shas.items():
        log(f"[launcher] {CDN}/{key}\n[launcher] sha256 {sha}")
    log(f"[launcher] update feed {CDN}/{FEED_KEY} -> {version} ({', '.join(sorted(platforms))})")
    return shas


def upload_installer(store: Store, installer: Path, version: str, notes: str = "", log=print,
                     now: datetime | None = None) -> str:
    """Windows: the NSIS installer is both the download and the update. Returns its SHA-256."""
    return publish(store, WINDOWS, version, installer, notes=notes, log=log, now=now)[installer_key(version)]


def upload_macos(store: Store, app_archive: Path, dmg: Path, version: str, notes: str = "", log=print,
                 now: datetime | None = None) -> dict[str, str]:
    """macOS: the .dmg for the site, the signed .app.tar.gz for the updater."""
    return publish(store, MACOS, version, app_archive, dmg, notes=notes, log=log, now=now)
