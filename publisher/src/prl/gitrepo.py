"""What git knows about source files that the file system alone can't tell us (#25)."""

from __future__ import annotations

import subprocess
from pathlib import Path

SYMLINK_MODE = "120000"


def repo_root(path: Path) -> Path | None:
    """Top of the git work tree containing `path`, or None outside any repo."""
    r = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None


def tracked_symlinks(root: Path) -> set[str]:
    """Repo-relative paths that git tracks as symlinks."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"],
                         check=True, capture_output=True).stdout.decode("utf-8")
    links = set()
    for entry in filter(None, out.split("\0")):
        meta, path = entry.split("\t", 1)
        if meta.split(" ", 1)[0] == SYMLINK_MODE:
            links.add(path)
    return links


def symlink_stubs(files: list[tuple[Path, str]]) -> list[str]:
    """Install paths whose source git tracks as a symlink but which is a plain file on disk.

    With core.symlinks=false, git writes a symlink as a tiny text file holding the target
    path; shipping that means shipping a broken asset (release 0.1.0 did, 544 times).
    """
    roots: dict[Path, set[str]] = {}
    # Per folder: (repo root, folder path relative to it) or None outside any repo.
    # Resolving once per folder rather than per file keeps 18k files to ~1 s.
    folders: dict[Path, tuple[Path, str] | None] = {}
    stubs = []
    for src, dest in files:
        if src.parent not in folders:
            parent = src.parent.resolve()
            root = next((r for r in roots if parent.is_relative_to(r)), None) or repo_root(src)
            if root is not None and root not in roots:
                roots[root] = tracked_symlinks(root)
            folders[src.parent] = None if root is None else (root, parent.relative_to(root).as_posix())
        where = folders[src.parent]
        if where is None:
            continue
        root, rel_dir = where
        rel = f"{rel_dir}/{src.name}" if rel_dir != "." else src.name
        if rel in roots[root] and not src.is_symlink():
            stubs.append(dest)
    return stubs
