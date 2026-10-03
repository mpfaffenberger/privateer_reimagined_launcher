"""release.toml: which files make up a release, and where they install.

    product = "privateer-reimagined"

    [launch]
    windows = "new_privateer.exe"

    [[source]]
    from = "${GAME_REPO}/build/new_privateer.exe"   # a file...
    to = "new_privateer.exe"

    [[source]]
    from = "${GAME_REPO}/assets"                    # ...or a directory (recursed)
    to = "assets"
    exclude = ["**/*.blend", "**/Thumbs.db"]        # globs relative to `from`

Relative `from` paths resolve against the config file's folder; ${VARS} come from the
environment, and an unset one is an error rather than a silently wrong path.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


@dataclass
class Source:
    src: Path
    dest: str
    exclude: list[str] = field(default_factory=list)


@dataclass
class ReleaseConfig:
    product: str
    launch: dict[str, str]
    sources: list[Source]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """`**/` = any directories (incl. none), `*` = within one segment, `?` = one char."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z", re.IGNORECASE)


def _expand(value: str) -> str:
    expanded = os.path.expandvars(value)
    if "$" in expanded:
        raise ValueError(f"unset environment variable in {value!r}")
    return expanded


def load(path: Path) -> ReleaseConfig:
    doc = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    sources = []
    for s in doc.get("source", []):
        src = Path(_expand(s["from"]))
        sources.append(Source(src if src.is_absolute() else base / src,
                              paths.check(s["to"]), list(s.get("exclude", []))))
    if not sources:
        raise ValueError(f"{path}: no [[source]] entries")
    return ReleaseConfig(doc["product"], dict(doc.get("launch", {})), sources)


def collect(cfg: ReleaseConfig) -> list[tuple[Path, str]]:
    """Every (source file, install path) pair, excludes applied, paths validated."""
    out: list[tuple[Path, str]] = []
    for s in cfg.sources:
        if s.src.is_file():
            out.append((s.src, s.dest))
            continue
        if not s.src.is_dir():
            raise FileNotFoundError(f"source {s.src} does not exist")
        rules = [glob_to_regex(p) for p in s.exclude]
        for f in sorted(s.src.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(s.src).as_posix()
            if any(r.match(rel) for r in rules):
                continue
            out.append((f, paths.check(f"{s.dest}/{rel}")))
    paths.check_unique([dest for _, dest in out])
    return out
