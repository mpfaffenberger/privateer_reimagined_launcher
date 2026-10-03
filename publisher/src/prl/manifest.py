"""Manifest + channel pointer: the publisher/launcher contract (docs/DESIGN.md)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from . import FORMAT, paths


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str
    enc: str          # "zstd" | "raw"
    stored_size: int

    def blob_key(self) -> str:
        return blob_key(self.sha256, self.enc)


def blob_key(sha256: str, enc: str) -> str:
    if enc not in ("zstd", "raw"):
        raise ValueError(f"unknown encoding {enc!r}")
    return f"blobs/{sha256[:2]}/{sha256}" + (".zst" if enc == "zstd" else "")


@dataclass
class Manifest:
    product: str
    version: str
    channel: str
    created_utc: str
    launch: dict[str, str]
    files: list[FileEntry] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        """Canonical bytes: sorted files, fixed key order, LF, trailing newline.

        Same inputs -> byte-identical output, so signatures and hashes are reproducible.
        """
        files = sorted(self.files, key=lambda f: f.path)
        for f in files:
            paths.check(f.path)
        paths.check_unique([f.path for f in files])
        doc = {
            "format": FORMAT,
            "product": self.product,
            "version": self.version,
            "channel": self.channel,
            "created_utc": self.created_utc,
            "launch": dict(sorted(self.launch.items())),
            "total_size": sum(f.size for f in files),
            "files": [asdict(f) for f in files],
        }
        return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")

    @staticmethod
    def from_bytes(data: bytes) -> "Manifest":
        doc = json.loads(data)
        if doc.get("format") != FORMAT:
            raise ValueError(f"unsupported manifest format {doc.get('format')!r}")
        files = [FileEntry(**f) for f in doc["files"]]
        for f in files:
            paths.check(f.path)
        paths.check_unique([f.path for f in files])
        return Manifest(doc["product"], doc["version"], doc["channel"], doc["created_utc"],
                        doc["launch"], files)


def manifest_key(version: str) -> str:
    paths.check(version)            # versions become object keys: no '/', '..', etc.
    if "/" in version:
        raise ValueError(f"version {version!r} must not contain '/'")
    return f"manifests/{version}.json"


def channel_key(channel: str) -> str:
    if not channel.replace("-", "").isalnum():
        raise ValueError(f"channel {channel!r} must be alphanumeric (dashes allowed)")
    return f"channels/{channel}.json"


def channel_pointer(channel: str, version: str, manifest_bytes: bytes) -> bytes:
    doc = {
        "format": FORMAT,
        "channel": channel,
        "version": version,
        "manifest": manifest_key(version),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    return (json.dumps(doc, indent=1) + "\n").encode("utf-8")
