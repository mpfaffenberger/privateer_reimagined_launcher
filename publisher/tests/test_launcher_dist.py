"""#27: the launcher installer is published under an immutable, versioned name.
#48: with its updater signature, and the update feed (latest.json) pointing at it."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from conftest import quiet
from prl import cli, launcher_dist
from prl.store import LocalStore, cache_control, content_type


def installer(tmp_path, version="0.1.0", data=b"MZ fake installer", sig="dW50cnVzdGVkIGNvbW1lbnQ6IHNpZw=="):
    path = tmp_path / f"Privateer Reimagined_{version}_x64-setup.exe"
    path.write_bytes(data)
    if sig is not None:
        path.with_name(path.name + ".sig").write_text(sig + "\n")
    return path


class RecordingStore(LocalStore):
    def __init__(self, root):
        super().__init__(root)
        self.writes = []

    def put_bytes(self, key, data):
        self.writes.append(key)
        super().put_bytes(key, data)

    def put_file(self, key, path):
        self.writes.append(key)
        super().put_file(key, path)


def test_upload_puts_it_under_the_versioned_name(tmp_path):
    store = LocalStore(tmp_path / "cdn")
    sha = launcher_dist.upload_installer(store, installer(tmp_path), "0.1.0", log=quiet)
    key = "launcher/PrivateerReimagined-Setup-0.1.0-x64.exe"
    assert store.get(key) == b"MZ fake installer"
    assert sha == hashlib.sha256(b"MZ fake installer").hexdigest()


def test_same_bytes_again_is_fine_different_bytes_are_refused(tmp_path):
    store = LocalStore(tmp_path / "cdn")
    launcher_dist.upload_installer(store, installer(tmp_path), "0.1.0", log=quiet)
    launcher_dist.upload_installer(store, installer(tmp_path), "0.1.0", log=quiet)
    with pytest.raises(FileExistsError, match="bump the launcher version"):
        launcher_dist.upload_installer(store, installer(tmp_path, data=b"MZ rebuilt"), "0.1.0", log=quiet)
    assert store.get("launcher/PrivateerReimagined-Setup-0.1.0-x64.exe") == b"MZ fake installer"


@pytest.mark.parametrize("version", ["0.1", "v0.1.0", "0.1.0/../x", ""])
def test_bad_versions_are_rejected(tmp_path, version):
    with pytest.raises(ValueError, match="must look like"):
        launcher_dist.upload_installer(LocalStore(tmp_path), installer(tmp_path), version, log=quiet)


def test_installer_and_version_must_agree(tmp_path):
    with pytest.raises(ValueError, match="doesn't look like the 0.2.0 launcher"):
        launcher_dist.upload_installer(LocalStore(tmp_path), installer(tmp_path, "0.1.0"), "0.2.0", log=quiet)


def test_cli(tmp_path):
    rc = cli.main(["upload-launcher", "--installer", str(installer(tmp_path)), "--version", "0.1.0",
                   "--notes", "Self-updates now.", "--to", str(tmp_path / "cdn")])
    assert rc == 0 and (tmp_path / "cdn/launcher/PrivateerReimagined-Setup-0.1.0-x64.exe").is_file()
    assert json.loads((tmp_path / "cdn/launcher/latest.json").read_text())["notes"] == "Self-updates now."


def test_feed_is_tauri_static_json_and_written_last(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    when = datetime(2026, 10, 4, 12, 30, 15, 999, tzinfo=timezone.utc)
    launcher_dist.upload_installer(store, installer(tmp_path, "0.3.0"), "0.3.0", "notes", log=quiet, now=when)
    key = "launcher/PrivateerReimagined-Setup-0.3.0-x64.exe"
    assert store.writes == [key, key + ".sig", "launcher/latest.json"]
    assert json.loads(store.get("launcher/latest.json")) == {
        "version": "0.3.0", "notes": "notes", "pub_date": "2026-10-04T12:30:15Z",
        "platforms": {"windows-x86_64": {"signature": "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZw==",
                                         "url": f"https://cdn.privateer-reimagined.com/{key}"}}}


def test_unsigned_installer_is_refused_before_anything_is_uploaded(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    with pytest.raises(FileNotFoundError, match="TAURI_SIGNING_PRIVATE_KEY"):
        launcher_dist.upload_installer(store, installer(tmp_path, sig=None), "0.1.0", log=quiet)
    assert store.writes == []


def test_the_feed_never_rolls_back(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    launcher_dist.upload_installer(store, installer(tmp_path, "0.10.0"), "0.10.0", log=quiet)
    store.writes.clear()
    with pytest.raises(ValueError, match="roll launchers back"):
        launcher_dist.upload_installer(store, installer(tmp_path, "0.9.0"), "0.9.0", log=quiet)   # numeric, not text
    assert store.writes == [] and launcher_dist.feed_version(store) == "0.10.0"
    launcher_dist.upload_installer(store, installer(tmp_path, "0.10.0"), "0.10.0", "fixed notes", log=quiet)
    assert json.loads(store.get("launcher/latest.json"))["notes"] == "fixed notes"   # same version: re-point ok


# --- macOS (#69): .dmg for the site, signed .app.tar.gz for the updater, one shared feed ---

def mac_files(tmp_path, version="0.3.2", sig="bWFjIHNpZw=="):
    d = tmp_path / f"mac-{version}"
    d.mkdir(exist_ok=True)
    archive = d / f"Privateer Reimagined_{version}_aarch64.app.tar.gz"
    archive.write_bytes(b"\x1f\x8b fake app archive " + version.encode())
    dmg = d / f"Privateer Reimagined_{version}_aarch64.dmg"
    dmg.write_bytes(b"fake dmg " + version.encode())
    if sig is not None:
        archive.with_name(archive.name + ".sig").write_text(sig + "\n")
    return archive, dmg


def test_macos_uploads_dmg_archive_sig_then_feed(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    archive, dmg = mac_files(tmp_path)
    shas = launcher_dist.upload_macos(store, archive, dmg, "0.3.2", "mac notes", log=quiet)
    dmg_key = "launcher/PrivateerReimagined-0.3.2-macos-arm64.dmg"
    arc_key = "launcher/PrivateerReimagined-0.3.2-macos-arm64.app.tar.gz"
    assert store.writes == [dmg_key, arc_key, arc_key + ".sig", "launcher/latest.json"]
    assert shas[dmg_key] == hashlib.sha256(dmg.read_bytes()).hexdigest()
    feed = json.loads(store.get("launcher/latest.json"))
    assert feed["platforms"] == {"darwin-aarch64": {
        "signature": "bWFjIHNpZw==", "url": f"https://cdn.privateer-reimagined.com/{arc_key}"}}


def test_both_platforms_share_one_feed_at_the_same_version(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    launcher_dist.upload_installer(store, installer(tmp_path, "0.3.2"), "0.3.2", log=quiet)
    archive, dmg = mac_files(tmp_path, "0.3.2")
    launcher_dist.upload_macos(store, archive, dmg, "0.3.2", log=quiet)
    feed = json.loads(store.get("launcher/latest.json"))
    assert feed["version"] == "0.3.2" and set(feed["platforms"]) == {"windows-x86_64", "darwin-aarch64"}


def test_a_newer_version_never_offers_another_platforms_older_build(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    archive, dmg = mac_files(tmp_path, "0.3.2")
    launcher_dist.upload_macos(store, archive, dmg, "0.3.2", log=quiet)
    launcher_dist.upload_installer(store, installer(tmp_path, "0.3.3"), "0.3.3", log=quiet)
    feed = json.loads(store.get("launcher/latest.json"))
    # The Mac entry was the 0.3.2 build: offering it as "0.3.3" would loop Macs on a re-install.
    assert feed["version"] == "0.3.3" and set(feed["platforms"]) == {"windows-x86_64"}


def test_unsigned_mac_archive_is_refused_before_anything_is_uploaded(tmp_path):
    store = RecordingStore(tmp_path / "cdn")
    archive, dmg = mac_files(tmp_path, sig=None)
    with pytest.raises(FileNotFoundError, match="tauri signer sign"):
        launcher_dist.upload_macos(store, archive, dmg, "0.3.2", log=quiet)
    assert store.writes == []


def test_cli_macos(tmp_path):
    archive, dmg = mac_files(tmp_path, "0.3.2")
    rc = cli.main(["upload-launcher", "--macos-archive", str(archive), "--macos-dmg", str(dmg),
                   "--version", "0.3.2", "--to", str(tmp_path / "cdn")])
    assert rc == 0 and (tmp_path / "cdn/launcher/PrivateerReimagined-0.3.2-macos-arm64.dmg").is_file()
    with pytest.raises(SystemExit):   # archive without dmg
        cli.main(["upload-launcher", "--macos-archive", str(archive), "--version", "0.3.2",
                  "--to", str(tmp_path / "cdn")])


def test_cache_headers():
    # #54: the feed changes every release; a cached copy would pin launchers to an old version.
    assert cache_control(launcher_dist.FEED_KEY) == "no-cache"
    assert cache_control("channels/stable.json") == cache_control("manifests/1.0.0.json") == "no-cache"
    immutable = "public, max-age=31536000, immutable"
    key = launcher_dist.installer_key("0.3.0")
    assert cache_control(key) == cache_control(key + ".sig") == cache_control("blobs/ab/abcdef.zst") == immutable


def test_content_types():
    assert content_type("channels/stable.json") == "application/json"
    assert content_type("launcher/X-Setup-0.1.0-x64.exe") == "application/vnd.microsoft.portable-executable"
    assert content_type("blobs/ab/abcdef.zst") == "application/octet-stream"
    assert content_type("launcher/X-0.3.2-macos-arm64.dmg") == "application/x-apple-diskimage"
    assert content_type("launcher/X-0.3.2-macos-arm64.app.tar.gz") == "application/gzip"
