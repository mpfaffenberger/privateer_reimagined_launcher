"""#130: each published release names the file whose downloads count it."""

from __future__ import annotations

import json

import pytest

from conftest import build, quiet
from prl import config, release, stats
from prl.manifest import Manifest
from prl.store import LocalStore, cache_control


def markers(store: LocalStore) -> dict:
    return json.loads(store.get(stats.MARKERS_KEY))["markers"]


def exe_key(m: Manifest) -> str:
    return next(iter([f for f in m.files if f.path == "new_privateer.exe"])).blob_key()


def test_publishing_records_the_executable_of_each_release(tmp_path, game, key):
    m1, store = build(tmp_path, game, key, "1.0.0")
    remote = LocalStore(tmp_path / "remote")
    release.publish(store, remote, "1.0.0", log=quiet)
    (game / "new_privateer.exe").write_bytes(b"MZ v2" * 100)
    m2, _ = build(tmp_path, game, key, "1.0.1", store)
    release.publish(store, remote, "1.0.1", log=quiet)
    got = markers(remote)
    assert list(got) == [exe_key(m1), exe_key(m2)], "one per release, newest last"
    assert {k: (v["channel"], v["version"]) for k, v in got.items()} == {
        exe_key(m1): ("stable", "1.0.0"), exe_key(m2): ("stable", "1.0.1")}
    assert all(len(v["published"]) == 10 for v in got.values()), "a day, nothing finer"


def test_an_unchanged_executable_moves_to_the_newer_release(tmp_path, game, key):
    m1, store = build(tmp_path, game, key, "1.0.0")
    remote = LocalStore(tmp_path / "remote")
    release.publish(store, remote, "1.0.0", log=quiet)
    (game / "assets" / "data" / "ships.json").write_text('{"tarsus": 2}\n')
    build(tmp_path, game, key, "1.0.1", store)
    release.publish(store, remote, "1.0.1", log=quiet)
    assert {k: v["version"] for k, v in markers(remote).items()} == {exe_key(m1): "1.0.1"}


def test_sign_publish_records_it_too(tmp_path, game, key):
    dst = LocalStore(tmp_path / "cdn")
    m, local = build(tmp_path / "ci", game, None, known=dst)
    release.publish(local, dst, "1.0.0", blobs_only=True, log=quiet)
    assert not dst.exists(stats.MARKERS_KEY), "nothing is live yet"
    release.sign_publish(local.get("manifests/1.0.0.json"), dst, key[0], log=quiet)
    assert list(markers(dst)) == [exe_key(m)]


def test_each_platform_counts_its_own_executable(tmp_path, game, key):
    m, _ = build(tmp_path, game, key)
    mac = Manifest(m.product, "1.0.0-macos", "stable-macos", m.created_utc,
                   {"windows": "new_privateer.exe", "macos": "assets/data/ships.json"}, m.files)
    assert stats.marker_key(m) == exe_key(m)
    assert stats.marker_key(mac) == next(iter([f for f in m.files if f.path == "assets/data/ships.json"])).blob_key()
    with pytest.raises(ValueError, match="launch target"):
        stats.marker_key(Manifest(m.product, "2", "stable-linux", m.created_utc, m.launch, m.files))


def test_markers_are_never_cached():
    assert cache_control(stats.MARKERS_KEY) == "no-cache"


@pytest.mark.parametrize("channel, os_name", [("stable", "windows"), ("stable-macos", "macos"),
                                              ("beta-linux", "linux"), ("stable-windows", "windows")])
def test_channel_os(channel, os_name):
    assert config.channel_os(channel) == os_name
