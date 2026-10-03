"""#25: git symlinks checked out as text must never ship."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import build
from prl import gitrepo


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          input=input).stdout.decode().strip()


@pytest.fixture()
def repo_game(game: Path) -> Path:
    """The fake game as a git repo with core.symlinks=false and one tracked symlink,
    checked out the way Git for Windows does it by default: as a text stub."""
    git(game, "init", "-q")
    git(game, "config", "core.symlinks", "false")
    target = b"../music/noise.bin"
    sha = git(game, "hash-object", "-w", "--stdin", input=target)
    git(game, "update-index", "--add", "--cacheinfo", f"120000,{sha},assets/data/link.bin")
    git(game, "checkout", "--", "assets/data/link.bin")
    assert (game / "assets/data/link.bin").read_bytes() == target   # the 18-byte stub
    return game


def test_stub_is_detected_and_the_build_refuses(tmp_path, repo_game, key):
    stubs = gitrepo.symlink_stubs([(repo_game / "assets/data/link.bin", "assets/data/link.bin"),
                                   (repo_game / "assets/data/ships.json", "assets/data/ships.json")])
    assert stubs == ["assets/data/link.bin"]
    with pytest.raises(ValueError, match="git symlinks checked out as plain text"):
        build(tmp_path / "out", repo_game, key)


def test_excluding_the_stub_lets_the_build_through(tmp_path, repo_game, key):
    (repo_game / "assets/data/link.bin").unlink()      # same effect as an exclude rule
    git(repo_game, "rm", "-q", "--cached", "assets/data/link.bin")
    m, _ = build(tmp_path / "out", repo_game, key)
    assert not any(f.path.endswith("link.bin") for f in m.files)


def test_a_real_symlink_is_followed_not_refused(tmp_path, repo_game):
    link = repo_game / "assets/data/link.bin"
    link.unlink()
    try:
        os.symlink("../music/noise.bin", link)
    except OSError:
        pytest.skip("this machine can't create symlinks (needs Developer Mode on Windows)")
    assert gitrepo.symlink_stubs([(link, "assets/data/link.bin")]) == []


def test_files_outside_any_repo_are_not_flagged(tmp_path, game):
    files = [(p, p.name) for p in (game / "assets").rglob("*") if p.is_file()]
    assert gitrepo.symlink_stubs(files) == []
