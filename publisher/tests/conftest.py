"""Shared fixtures + helpers for the publisher tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prl import release, sign
from prl.store import LocalStore

CREATED = "2026-10-03T00:00:00Z"


def quiet(*_args, **_kwargs) -> None:
    pass


@pytest.fixture()
def key(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "signing_key.pem"
    return path, sign.keygen(path)


@pytest.fixture()
def game(tmp_path: Path) -> Path:
    """A tiny fake game: exe, compressible text, incompressible noise, an excluded source."""
    root = tmp_path / "game"
    (root / "assets" / "data").mkdir(parents=True)
    (root / "assets" / "music").mkdir(parents=True)
    (root / "new_privateer.exe").write_bytes(b"MZ" + b"\x00" * 4096)
    (root / "assets" / "data" / "ships.json").write_text('{"tarsus": 1}\n' * 400)
    (root / "assets" / "music" / "noise.bin").write_bytes(os.urandom(64 * 1024))
    (root / "assets" / "data" / "copy_of_ships.json").write_text('{"tarsus": 1}\n' * 400)  # dup content
    (root / "assets" / "model.blend").write_bytes(b"blender source, never shipped")
    return root


def write_config(tmp_path: Path, game: Path) -> Path:
    cfg = tmp_path / "release.toml"
    cfg.write_text(f'''
product = "privateer-reimagined"
[launch]
windows = "new_privateer.exe"
[[source]]
from = "{(game / 'new_privateer.exe').as_posix()}"
to = "new_privateer.exe"
[[source]]
from = "{(game / 'assets').as_posix()}"
to = "assets"
exclude = ["**/*.blend"]
''')
    return cfg


def build(tmp_path, game, key, version="1.0.0", store=None, **kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = store or LocalStore(tmp_path / "store")
    key_path = key[0] if key else None
    m = release.build(write_config(tmp_path, game), version, "stable", store, key_path,
                      created_utc=CREATED, log=quiet, **kwargs)
    return m, store
