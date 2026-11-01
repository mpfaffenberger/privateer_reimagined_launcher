"""prl publisher tests: the guarantees the launcher (#3) relies on."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import zstandard

from conftest import CREATED, build, write_config
from prl import config, paths, release, sign
from prl.manifest import Manifest, channel_key, manifest_key
from prl.store import LocalStore


# ---- path rules --------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "/etc/passwd", "C:/x", "c:x", "a/../b", "../x", "a//b",
                                 "./a", "a/.", "a\\b", "a/"])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(paths.UnsafePath):
        paths.check(bad)


def test_safe_paths_accepted():
    assert paths.check("assets/music/menu.wav") == "assets/music/menu.wav"


def test_case_insensitive_collision_rejected():
    with pytest.raises(paths.UnsafePath):
        paths.check_unique(["assets/A.png", "assets/a.PNG"])


def test_excludes_use_globstar():
    rx = config.glob_to_regex("**/*.blend")
    assert rx.match("model.blend") and rx.match("a/b/model.BLEND")
    assert not rx.match("a/model.blend1")


# ---- build -------------------------------------------------------------------

def test_build_manifest_contents(tmp_path, game, key):
    m, _ = build(tmp_path, game, key)
    got = {f.path: f for f in m.files}
    assert set(got) == {"new_privateer.exe", "assets/data/ships.json",
                        "assets/data/copy_of_ships.json", "assets/music/noise.bin"}  # .blend excluded
    assert got["assets/data/ships.json"].enc == "zstd"      # text compresses well
    assert got["assets/music/noise.bin"].enc == "raw"       # random data doesn't
    assert got["assets/music/noise.bin"].stored_size == got["assets/music/noise.bin"].size
    data = (game / "assets/data/ships.json").read_bytes()
    assert got["assets/data/ships.json"].sha256 == hashlib.sha256(data).hexdigest()


def test_manifest_bytes_are_deterministic(tmp_path, game, key):
    m1, _ = build(tmp_path / "a", game, key)
    m2, _ = build(tmp_path / "b", game, key)
    assert m1.to_bytes() == m2.to_bytes()
    paths_in_order = [f["path"] for f in json.loads(m1.to_bytes())["files"]]
    assert paths_in_order == sorted(paths_in_order)


def test_blobs_decode_to_original_content(tmp_path, game, key):
    m, store = build(tmp_path, game, key)
    for f in m.files:
        raw = store.get(f.blob_key())
        body = zstandard.ZstdDecompressor().decompressobj().decompress(raw) if f.enc == "zstd" else raw
        assert hashlib.sha256(body).hexdigest() == f.sha256 and len(body) == f.size


def test_duplicate_content_stored_once(tmp_path, game, key):
    m, store = build(tmp_path, game, key)
    assert len(store.keys("blobs/")) == len({f.sha256 for f in m.files}) == 3


def test_second_release_only_adds_changed_content(tmp_path, game, key):
    _, store = build(tmp_path, game, key, "1.0.0")
    before = store.keys("blobs/")
    (game / "assets" / "data" / "ships.json").write_text('{"tarsus": 2}\n' * 400)
    build(tmp_path, game, key, "1.0.1", store)
    assert len(store.keys("blobs/") - before) == 1


def test_versions_are_immutable(tmp_path, game, key):
    _, store = build(tmp_path, game, key, "1.0.0")
    build(tmp_path, game, key, "1.0.0", store)               # identical rebuild is fine
    (game / "new_privateer.exe").write_bytes(b"MZ changed")
    with pytest.raises(FileExistsError):
        build(tmp_path, game, key, "1.0.0", store)


def test_lfs_pointer_files_are_refused_before_anything_is_written(tmp_path, game, key):
    pointer = (b"version https://git-lfs.github.com/spec/v1\n"
               b"oid sha256:" + b"ab" * 32 + b"\nsize 1221769\n")
    (game / "assets" / "cockpit.png").write_bytes(pointer)
    store = LocalStore(tmp_path / "store")
    with pytest.raises(ValueError, match=r"1 file\(s\) are Git LFS pointers[\s\S]*assets/cockpit.png"):
        build(tmp_path, game, key, "1.0.0", store)
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").rglob("*.json"))
    # Real content that merely mentions LFS later on (or a large file) is fine.
    (game / "assets" / "cockpit.png").write_bytes(b"\x89PNG" + pointer)
    build(tmp_path, game, key, "1.0.0", store)


def test_unset_env_var_in_config_is_an_error(tmp_path):
    cfg = tmp_path / "release.toml"
    cfg.write_text('product="p"\n[[source]]\nfrom="${PRL_SURELY_UNSET_VAR}/x"\nto="x"\n')
    with pytest.raises(ValueError):
        config.load(cfg)


# ---- signing -----------------------------------------------------------------

def test_signature_verifies_and_detects_tampering(tmp_path, game, key):
    m, store = build(tmp_path, game, key)
    data = store.get(manifest_key("1.0.0"))
    sig = store.get(manifest_key("1.0.0") + ".sig")
    assert sign.verify(key[1], data, sig)
    assert not sign.verify(key[1], data.replace(b'"stable"', b'"beta!!"'), sig)
    _, other_pub = Path(tmp_path / "k2.pem"), sign.keygen(tmp_path / "k2.pem")
    assert not sign.verify(other_pub, data, sig)


def test_keygen_never_overwrites(tmp_path, key):
    with pytest.raises(FileExistsError):
        sign.keygen(key[0])


# ---- publish + verify --------------------------------------------------------

class RecordingStore(LocalStore):
    def __init__(self, root):
        super().__init__(root)
        self.order: list[str] = []

    def put_bytes(self, k, data):
        self.order.append(k)
        super().put_bytes(k, data)

    def put_file(self, k, path):
        self.order.append(k)
        super().put_file(k, path)


def test_publish_uploads_missing_blobs_then_manifest_then_pointer_last(tmp_path, game, key):
    _, store = build(tmp_path, game, key)
    remote = RecordingStore(tmp_path / "remote")
    stats = release.publish(store, remote, "1.0.0", log=lambda *_: None)
    assert stats["uploaded"] == 3
    assert remote.order[-1] == channel_key("stable")
    assert remote.order[-4:-1] == [manifest_key("1.0.0"), manifest_key("1.0.0") + ".sig",
                                   "stats/markers.json"]
    assert all(k.startswith("blobs/") for k in remote.order[:-4])
    assert release.verify(remote, "stable", key[1], deep=True, log=lambda *_: None) == []


def test_republish_and_next_release_upload_only_new_blobs(tmp_path, game, key):
    _, store = build(tmp_path, game, key, "1.0.0")
    remote = LocalStore(tmp_path / "remote")
    release.publish(store, remote, "1.0.0", log=lambda *_: None)
    assert release.publish(store, remote, "1.0.0", log=lambda *_: None)["uploaded"] == 0
    (game / "new_privateer.exe").write_bytes(b"MZ v2" * 100)
    build(tmp_path, game, key, "1.0.1", store)
    assert release.publish(store, remote, "1.0.1", log=lambda *_: None)["uploaded"] == 1
    pointer = json.loads(remote.get(channel_key("stable")))
    assert pointer["version"] == "1.0.1"
    assert release.verify(remote, "stable", key[1], deep=True, log=lambda *_: None) == []


def test_verify_flags_wrong_key_and_missing_blob(tmp_path, game, key):
    m, store = build(tmp_path, game, key)
    remote = LocalStore(tmp_path / "remote")
    release.publish(store, remote, "1.0.0", log=lambda *_: None)
    other = sign.keygen(tmp_path / "other.pem")
    assert any("signature" in p for p in release.verify(remote, "stable", other, log=lambda *_: None))
    (remote.local_path(m.files[0].blob_key())).unlink()
    assert any("missing blob" in p for p in release.verify(remote, "stable", key[1], log=lambda *_: None))


def test_manifest_round_trips(tmp_path, game, key):
    m, _ = build(tmp_path, game, key)
    assert Manifest.from_bytes(m.to_bytes()).to_bytes() == m.to_bytes()
