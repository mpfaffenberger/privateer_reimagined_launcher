"""Git LFS pointers (#19, #21).

An un-pulled LFS file is a tiny text stub:

    version https://git-lfs.github.com/spec/v1
    oid sha256:<64 hex>
    size <bytes>

The oid IS the SHA-256 of the real content, i.e. exactly our manifest hash, so a release
can be described from pointers alone and only content the CDN lacks needs pulling.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .gitrepo import repo_root

POINTER_HEADER = b"version https://git-lfs.github.com/spec/"
POINTER_MAX = 1024
_OID = re.compile(rb"^oid sha256:([0-9a-f]{64})$", re.M)
_SIZE = re.compile(rb"^size (\d+)$", re.M)
# `git lfs pull --include` takes comma-separated gitignore-style patterns, so these
# characters can't be passed through as literal paths. Fail closed instead of guessing.
_UNSAFE_FOR_INCLUDE = re.compile(r"[,*?\[\]!#\\]|^\s|\s$")
_MAX_INCLUDE_CHARS = 6000   # stay far below Windows' 32k command-line limit


def read_pointer(path: Path) -> tuple[str, int] | None:
    """(sha256, size) if `path` is an LFS pointer, else None."""
    if path.stat().st_size > POINTER_MAX:
        return None
    data = path.read_bytes()
    if not data.startswith(POINTER_HEADER):
        return None
    oid, size = _OID.search(data), _SIZE.search(data)
    if not (oid and size):
        raise ValueError(f"{path}: malformed Git LFS pointer")
    return oid.group(1).decode(), int(size.group(1))


def find_pointers(files: list[tuple[Path, str]]) -> list[str]:
    """Install paths whose source is an LFS pointer instead of real content."""
    return [dest for src, dest in files if read_pointer(src)]


def _batches(items: list[str], max_chars: int) -> list[list[str]]:
    """Split into comma-joined groups no longer than `max_chars`."""
    batches: list[list[str]] = [[]]
    length = 0
    for item in items:
        if batches[-1] and length + len(item) + 1 > max_chars:
            batches.append([])
            length = 0
        batches[-1].append(item)
        length += len(item) + 1
    return [b for b in batches if b]


def pull(paths: list[Path], log=print) -> None:
    """Replace exactly these pointer files with their real content (`git lfs pull`).

    Callers must re-hash afterwards: this trusts git-lfs to fetch, not to be right.
    """
    by_repo: dict[Path, list[str]] = {}
    for p in paths:
        root = repo_root(p)
        if root is None:
            raise ValueError(f"{p} is an LFS pointer outside any git repo; can't pull it")
        rel = p.resolve().relative_to(root).as_posix()
        if _UNSAFE_FOR_INCLUDE.search(rel):
            raise ValueError(f"can't pass {rel!r} to `git lfs pull --include` literally")
        by_repo.setdefault(root, []).append(rel)
    for root, rels in by_repo.items():
        for batch in _batches(rels, _MAX_INCLUDE_CHARS):
            subprocess.run(["git", "-C", str(root), "lfs", "pull", "--include", ",".join(batch)],
                           check=True)
    log(f"[lfs] pulled {len(paths)} file(s)")
