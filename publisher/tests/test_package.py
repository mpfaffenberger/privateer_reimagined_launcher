"""Game #1508: `prl package` turns a built release into one standalone .tar.gz."""

from __future__ import annotations

import hashlib
import tarfile

import pytest

from conftest import build, quiet
from prl import cli, package
from prl.manifest import manifest_key

NAME = "privateer-reimagined-1.0.0-linux-x86_64"


def built(tmp_path, game):
    manifest, store = build(tmp_path, game, None)
    return manifest, store, store.get(manifest_key(manifest.version))


def test_every_file_is_under_the_archive_name_with_its_bytes_and_mode(tmp_path, game):
    manifest, store, data = built(tmp_path, game)
    out = package.package(data, store, tmp_path / f"{NAME}.tar.gz", log=quiet)
    with tarfile.open(out) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {f"{NAME}/{f.path}" for f in manifest.files}
        for f in manifest.files:
            member = members[f"{NAME}/{f.path}"]
            assert hashlib.sha256(tar.extractfile(member).read()).hexdigest() == f.sha256
            assert member.mode == (0o755 if f.path == "new_privateer.exe" else 0o644)


def test_the_same_release_gives_the_same_archive_bytes(tmp_path, game):
    _, store, data = built(tmp_path, game)
    one = package.package(data, store, tmp_path / "a" / f"{NAME}.tar.gz", log=quiet)
    two = package.package(data, store, tmp_path / "b" / f"{NAME}.tar.gz", log=quiet)
    assert one.read_bytes() == two.read_bytes()


def test_a_blob_that_does_not_match_the_manifest_is_refused(tmp_path, game):
    manifest, store, data = built(tmp_path, game)
    noise = next(f for f in manifest.files if f.path.endswith("noise.bin"))
    assert noise.enc == "raw"   # incompressible, so its blob is the file itself
    blob = store.local_path(noise.blob_key())
    blob.write_bytes(bytes(len(blob.read_bytes())))
    out = tmp_path / f"{NAME}.tar.gz"
    with pytest.raises(ValueError, match="does not match the manifest"):
        package.package(data, store, out, log=quiet)
    assert not out.exists() and not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("name", ["game.zip", ".tar.gz", "game.tar", "..tar.gz", "...tar.gz"])
def test_the_archive_must_be_a_named_tar_gz(tmp_path, game, name):
    _, store, data = built(tmp_path, game)
    with pytest.raises(ValueError, match="must be named"):
        package.package(data, store, tmp_path / name, log=quiet)


def test_cli(tmp_path, game):
    manifest, store, data = built(tmp_path, game)
    path = tmp_path / "manifest.json"
    path.write_bytes(data)
    out = tmp_path / f"{NAME}.tar.gz"
    assert cli.main(["package", "--manifest", str(path), "--from", str(store.root), "--out", str(out)]) == 0
    with tarfile.open(out) as tar:
        assert len(tar.getmembers()) == len(manifest.files)
