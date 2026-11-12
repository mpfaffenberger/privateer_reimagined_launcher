"""`prl package` (#1508): one release as a standalone .tar.gz, for players who run the game
without the launcher (a Steam Deck's Desktop Mode, or a non-Steam game shortcut).

Made from a manifest and the blobs already in a store (R2 in CI), never from the game repo, so
it needs no Git LFS content and holds exactly the bytes the launcher would install: every file
is checked against the manifest on the way in. `<name>.tar.gz` unpacks into `<name>/`, the
launch target is executable, and the same release always gives the same archive bytes.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import os
import tarfile
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .manifest import FileEntry, Manifest
from .release import file_content
from .store import Store

SUFFIX = ".tar.gz"


def checked_content(store: Store, f: FileEntry) -> bytes:
    body = file_content(store, f)
    if len(body) != f.size or hashlib.sha256(body).hexdigest() != f.sha256:
        raise ValueError(f"the blob for {f.path} does not match the manifest")
    return body


def package(manifest_bytes: bytes, store: Store, out: Path, workers: int = 8, log=print) -> Path:
    """Write the release `manifest_bytes` describes to `out` (`<name>.tar.gz`)."""
    root = out.name[: -len(SUFFIX)]
    # The root becomes every member's first path segment: `..` would point outside the
    # folder it's unpacked in, and `.` or nothing would lose the promised top-level folder.
    if not out.name.endswith(SUFFIX) or root in ("", ".", ".."):
        raise ValueError(f"{out.name!r}: the package must be named <name>{SUFFIX}")
    manifest = Manifest.from_bytes(manifest_bytes)
    executables = set(manifest.launch.values())
    mtime = int(dt.datetime.strptime(manifest.created_utc, "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=dt.UTC).timestamp())
    files = sorted(manifest.files, key=lambda f: f.path)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    # Fetch in parallel, write in order, and keep only a window of files in memory.
    window: deque[tuple[FileEntry, Future[bytes]]] = deque()
    try:
        # mtime=0 and no file name in the gzip header: same release, same archive bytes.
        with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar, \
                ThreadPoolExecutor(workers) as pool:
            def write(f: FileEntry, body: bytes) -> None:
                info = tarfile.TarInfo(f"{root}/{f.path}")
                info.size, info.mtime = len(body), mtime
                info.mode = 0o755 if f.path in executables else 0o644
                tar.addfile(info, io.BytesIO(body))

            for f in files:
                window.append((f, pool.submit(checked_content, store, f)))
                if len(window) >= 2 * workers:
                    done, fut = window.popleft()
                    write(done, fut.result())
            while window:
                done, fut = window.popleft()
                write(done, fut.result())
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"[package] {out} ({len(files)} files, {manifest.version}, {out.stat().st_size / 1e9:.2f} GB)")
    return out
