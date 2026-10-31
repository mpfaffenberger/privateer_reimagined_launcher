"""Download counts (#130): which file marks a download of which release.

A release's game executable is new in that release (it's rebuilt every time) and belongs to one
platform, and every install or update to the release downloads it exactly once. So
`stats/markers.json` maps the executable's blob key to its release, and the site Worker
(`worker/`, docs/DOWNLOAD_COUNTS.md) counts downloads per release from R2's own per-file
totals. Nothing about players is involved: only how often one file was fetched.
"""

from __future__ import annotations

import datetime as dt
import json

from . import FORMAT, config
from .manifest import Manifest
from .store import Store

MARKERS_KEY = "stats/markers.json"


def marker_key(manifest: Manifest) -> str:
    """The blob key of the file this release's launchers run."""
    target = manifest.launch.get(config.channel_os(manifest.channel))
    entry = next(iter([f for f in manifest.files if f.path == target]), None)
    if entry is None:
        raise ValueError(f"{manifest.version}: its launch target {target!r} isn't in the release")
    return entry.blob_key()


def record(dst: Store, manifest: Manifest, published: str | None = None, log=print) -> None:
    """Add this release to `stats/markers.json`, newest last. An executable a later release
    ships unchanged belongs to that later release from then on."""
    doc = json.loads(dst.get(MARKERS_KEY)) if dst.exists(MARKERS_KEY) else {"format": FORMAT, "markers": {}}
    key = marker_key(manifest)
    doc["markers"].pop(key, None)
    doc["markers"][key] = {
        "channel": manifest.channel,
        "version": manifest.version,
        "published": published or dt.datetime.now(dt.UTC).date().isoformat(),
    }
    dst.put_bytes(MARKERS_KEY, (json.dumps(doc, indent=1) + "\n").encode("utf-8"))
    log(f"[stats] {manifest.channel} {manifest.version}: downloads counted by {key}")
