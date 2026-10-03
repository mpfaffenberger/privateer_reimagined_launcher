"""Install-path rules shared with the launcher (docs/DESIGN.md, "Manifest" rules).

The launcher re-checks every path, but the publisher refuses to ever emit a bad one.
"""

from __future__ import annotations

import re

_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafePath(ValueError):
    """A manifest path that could escape the install directory or collide on Windows."""


def check(path: str) -> str:
    """Return `path` if it is a safe relative install path, else raise UnsafePath."""
    if not path:
        raise UnsafePath("empty path")
    if "\\" in path:
        raise UnsafePath(f"backslash in {path!r} (use '/')")
    if path.startswith("/") or _DRIVE.match(path):
        raise UnsafePath(f"absolute path {path!r}")
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            raise UnsafePath(f"bad segment {segment!r} in {path!r}")
    return path


def check_unique(paths: list[str]) -> None:
    """Reject paths that differ only by case: they collide on Windows/macOS file systems."""
    seen: dict[str, str] = {}
    for p in paths:
        key = p.casefold()
        if key in seen:
            raise UnsafePath(f"{p!r} collides with {seen[key]!r} on case-insensitive file systems")
        seen[key] = p
