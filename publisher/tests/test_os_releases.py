"""#66: one release.toml describes every OS; each OS's release goes to its own channel."""

from __future__ import annotations

import json

import pytest

from conftest import CREATED, quiet
from prl import config, release
from prl.store import LocalStore


def two_os_config(tmp_path, game):
    (game / "new_privateer").write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 4096)   # a Mach-O stand-in
    cfg = tmp_path / "release.toml"
    cfg.write_text(f'''
product = "privateer-reimagined"
[launch]
windows = "new_privateer.exe"
macos = "new_privateer"
[[source]]
os = "windows"
from = "{(game / 'new_privateer.exe').as_posix()}"
to = "new_privateer.exe"
[[source]]
os = "macos"
from = "{(game / 'new_privateer').as_posix()}"
to = "new_privateer"
[[source]]
from = "{(game / 'assets').as_posix()}"
to = "assets"
exclude = ["**/*.blend"]
''')
    return cfg


def build(tmp_path, cfg, channel, os_name, version="1.0.0", store=None):
    store = store or LocalStore(tmp_path / "store")
    return release.build(cfg, version, channel, store, None, created_utc=CREATED, os_name=os_name, log=quiet), store


def test_each_os_gets_its_binary_shared_assets_and_only_its_launch_entry(tmp_path, game):
    cfg = two_os_config(tmp_path, game)
    win, store = build(tmp_path, cfg, "stable", "windows", "1.0.0")
    mac, _ = build(tmp_path, cfg, "stable-macos", "macos", "1.0.0-macos", store)
    win_paths = {f.path for f in win.files}
    mac_paths = {f.path for f in mac.files}
    assert "new_privateer.exe" in win_paths and "new_privateer" not in win_paths
    assert "new_privateer" in mac_paths and "new_privateer.exe" not in mac_paths
    assert win_paths - {"new_privateer.exe"} == mac_paths - {"new_privateer"}   # same assets
    assert json.loads(win.to_bytes())["launch"] == {"windows": "new_privateer.exe"}
    assert json.loads(mac.to_bytes())["launch"] == {"macos": "new_privateer"}
    win_assets = {f.sha256 for f in win.files if f.path.startswith("assets/")}
    mac_assets = {f.sha256 for f in mac.files if f.path.startswith("assets/")}
    assert win_assets and win_assets == mac_assets   # same blobs: stored once for both OSes


@pytest.mark.parametrize("channel, os_name", [
    ("stable", "macos"),          # would offer a Mac build to every Windows launcher
    ("stable-macos", "windows"),  # and a Windows build to every Mac
    ("beta-linux", "macos"),
])
def test_a_build_only_goes_to_its_own_oss_channel(tmp_path, game, channel, os_name):
    with pytest.raises(ValueError, match="must go to"):
        build(tmp_path, two_os_config(tmp_path, game), channel, os_name)
    assert not (tmp_path / "store").exists()   # refused before anything was built


def test_the_default_is_still_a_windows_build_on_stable(tmp_path, game):
    """release.yml calls prl build without --os; that must keep producing today's release."""
    m = release.build(two_os_config(tmp_path, game), "1.0.0", "stable", LocalStore(tmp_path / "s"), None,
                      created_utc=CREATED, log=quiet)
    assert json.loads(m.to_bytes())["launch"] == {"windows": "new_privateer.exe"}


def test_config_errors(tmp_path, game):
    cfg = two_os_config(tmp_path, game)
    with pytest.raises(ValueError, match="no linux entry"):
        config.load(cfg, "linux")
    with pytest.raises(ValueError, match="unknown OS"):
        config.load(cfg, "amiga")
    bad = tmp_path / "bad.toml"
    bad.write_text(cfg.read_text().replace('os = "macos"', 'os = "macosx"'))
    with pytest.raises(ValueError, match="unknown OS 'macosx'"):
        config.load(bad, "windows")


def test_a_launch_target_missing_from_the_release_is_refused(tmp_path, game):
    cfg = two_os_config(tmp_path, game)
    cfg.write_text(cfg.read_text().replace('macos = "new_privateer"', 'macos = "Privateer.app"'))
    with pytest.raises(ValueError, match="isn't in the release"):
        build(tmp_path, cfg, "stable-macos", "macos")
