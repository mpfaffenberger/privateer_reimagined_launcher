"""#27: the launcher installer is published under an immutable, versioned name.
#48: with its updater signature, and the update feed (latest.json) pointing at it."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from conftest import quiet
from prl import cli, launcher_dist
from prl.store import LocalStore, content_type


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
    with pytest.raises(ValueError, match="doesn't look like the 0.2.0 installer"):
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


def test_content_types():
    assert content_type("channels/stable.json") == "application/json"
    assert content_type("launcher/X-Setup-0.1.0-x64.exe") == "application/vnd.microsoft.portable-executable"
    assert content_type("blobs/ab/abcdef.zst") == "application/octet-stream"
