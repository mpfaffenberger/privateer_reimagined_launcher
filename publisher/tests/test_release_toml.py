"""The repo's own release.toml, against a fake game checkout laid out like the real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from prl import config

RELEASE_TOML = Path(__file__).resolve().parents[2] / "release.toml"


@pytest.fixture()
def shipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Install paths the real release.toml ships for Windows from a fake GAME_REPO."""
    repo = tmp_path / "new_privateer"
    files = [
        "build/new_privateer.exe",
        "assets/meshes/ships_wcnews/brdswrdx.obj",
        "assets/meshes/ships_wcnews/brdswrdx.materials.json",
        "assets/meshes/ships_wcnews/brdswrdx.npmesh",
        "assets/meshes/planets/sphere.obj",
        "assets/meshes/planets/sphere.npmesh",
        "assets/debug/forward_axis_marker.npmesh",
        "assets/speech/flight_by_speaker/kroiz/line.mp3",
        "assets/data/fixers.json",
    ]
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_bytes(b"x")
    monkeypatch.setenv("GAME_REPO", str(repo))
    return {dest for _, dest in config.collect(config.load(RELEASE_TOML, "windows"))}


def test_runtime_mesh_caches_are_left_to_the_game(shipped):
    """#118: the game rebuilds a .npmesh it thinks is older than its .obj, with new bytes. A
    shipped cache then reads as a modified release file and the launcher re-offers the version
    that's already installed, again each time another ship's cache is rebuilt."""
    assert not {p for p in shipped if p.startswith("assets/meshes/") and p.endswith(".npmesh")}
    assert {"assets/meshes/ships_wcnews/brdswrdx.obj", "assets/meshes/planets/sphere.obj",
            "assets/meshes/ships_wcnews/brdswrdx.materials.json"} <= shipped


def test_caches_without_a_source_mesh_still_ship(shipped):
    """No .obj beside it: the game never rebuilds it, and it's the only copy of that mesh."""
    assert "assets/debug/forward_axis_marker.npmesh" in shipped


def test_the_rest_of_the_release_is_unchanged(shipped):
    assert {"new_privateer.exe", "assets/data/fixers.json"} <= shipped
    assert not any(p.startswith("assets/speech/flight_by_speaker/") for p in shipped)
