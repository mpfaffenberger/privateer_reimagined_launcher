"""#27: the launcher installer is published under an immutable, versioned name."""

from __future__ import annotations

import hashlib

import pytest

from conftest import quiet
from prl import cli, launcher_dist
from prl.store import LocalStore, content_type


def installer(tmp_path, version="0.1.0", data=b"MZ fake installer"):
    path = tmp_path / f"Privateer Reimagined_{version}_x64-setup.exe"
    path.write_bytes(data)
    return path


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
                   "--to", str(tmp_path / "cdn")])
    assert rc == 0 and (tmp_path / "cdn/launcher/PrivateerReimagined-Setup-0.1.0-x64.exe").is_file()


def test_content_types():
    assert content_type("channels/stable.json") == "application/json"
    assert content_type("launcher/X-Setup-0.1.0-x64.exe") == "application/vnd.microsoft.portable-executable"
    assert content_type("blobs/ab/abcdef.zst") == "application/octet-stream"
