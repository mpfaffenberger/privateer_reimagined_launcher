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

One file describes every platform (#66): an OS, plus the CPU for ARM64 Linux (#162), whose
game can't run on the Steam Deck's x86_64 Linux or the other way round. A source with
`os = "macos"` is only in that platform's release, `os = ["linux", "linux-arm64"]` in each one
listed, and one without `os` in all of them. `[launch]` has an entry per platform, and a
release keeps only its own.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import paths


OSES = ("windows", "macos", "linux", "linux-arm64")   # as prl-core's format::PLATFORM names them


@dataclass
class Source:
    src: Path
    dest: str
    exclude: list[str] = field(default_factory=list)


def check_os(os_name: str) -> str:
    if os_name not in OSES:
        raise ValueError(f"unknown OS {os_name!r}; expected one of {', '.join(OSES)}")
    return os_name


def channel_os(channel: str) -> str:
    """The platform whose launchers read `channel`: Windows reads the bare name (`stable`),
    others `<channel>-<os>` (prl-core's platform_channel)."""
    return next((o for o in OSES if channel.endswith(f"-{o}")), "windows")


def check_channel_for_os(channel: str, os_name: str) -> str:
    """Publishing an OS's build to another OS's channel would offer it to launchers on the
    wrong OS, which would replace a working install."""
    check_os(os_name)
    if channel_os(channel) != os_name:
        want = "a channel without an OS suffix" if os_name == "windows" else f"a channel ending in -{os_name}"
        raise ValueError(f"a {os_name} build must go to {want}, not {channel!r}")
    return channel


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


def source_oses(value: str | list[str]) -> list[str]:
    """A source's `os`: one platform or a list of them, each checked."""
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names:
        raise ValueError(f"a source's os must be a platform or a non-empty list of them, not {value!r}")
    return [check_os(n) for n in names]


def _expand(value: str) -> str:
    expanded = os.path.expandvars(value)
    if "$" in expanded:
        raise ValueError(f"unset environment variable in {value!r}")
    return expanded


def load(path: Path, os_name: str = "windows") -> ReleaseConfig:
    """The release for one OS: its sources and its launch entry only."""
    check_os(os_name)
    doc = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    sources = []
    for s in doc.get("source", []):
        if "os" in s and os_name not in source_oses(s["os"]):
            continue
        # Expand only what this OS ships: the other OS's build output needn't exist here.
        src = Path(_expand(s["from"]))
        sources.append(Source(src if src.is_absolute() else base / src,
                              paths.check(s["to"]), list(s.get("exclude", []))))
    if not sources:
        raise ValueError(f"{path}: no [[source]] entries for {os_name}")
    launch = dict(doc.get("launch", {}))
    for name in launch:
        check_os(name)
    if os_name not in launch:
        raise ValueError(f"{path}: [launch] has no {os_name} entry")
    return ReleaseConfig(doc["product"], {os_name: paths.check(launch[os_name])}, sources)


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
