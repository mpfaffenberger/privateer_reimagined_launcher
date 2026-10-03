"""Content stores with the docs/DESIGN.md layout: a local directory or Cloudflare R2."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol


class Store(Protocol):
    def keys(self, prefix: str) -> set[str]: ...
    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> bytes: ...
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def put_file(self, key: str, path: Path) -> None: ...
    def local_path(self, key: str) -> Path | None: ...


class LocalStore:
    """A directory with the store layout. Also the publisher's build cache."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*key.split("/"))

    def keys(self, prefix: str) -> set[str]:
        base = self._path(prefix)
        if not base.exists():
            return set()
        return {p.relative_to(self.root).as_posix() for p in base.rglob("*")
                if p.is_file() and not p.name.endswith(".tmp")}

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def put_bytes(self, key: str, data: bytes) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)            # atomic: readers never see a partial object

    def put_file(self, key: str, path: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(path, tmp)
        os.replace(tmp, dest)

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


class R2Store:
    """Cloudflare R2 through its S3-compatible API. Credentials come from the environment
    (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET), never the repo."""

    def __init__(self) -> None:
        import boto3  # only needed for real publishing

        missing = [v for v in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
                   if not os.environ.get(v)]
        if missing:
            raise RuntimeError("missing R2 environment variables: " + ", ".join(missing))
        self.bucket = os.environ["R2_BUCKET"]
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def keys(self, prefix: str) -> set[str]:
        out: set[str] = set()
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            out.update(obj["Key"] for obj in page.get("Contents", []))
        return out

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put_bytes(self, key: str, data: bytes) -> None:
        # Manifests and pointers must not be served stale by the CDN after a publish.
        cache = "no-cache" if key.startswith(("channels/", "manifests/")) else "public, max-age=31536000, immutable"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, CacheControl=cache)

    def put_file(self, key: str, path: Path) -> None:
        # Blobs are content-addressed, so they never change: cache them forever.
        self.s3.upload_file(str(path), self.bucket, key,
                            ExtraArgs={"CacheControl": "public, max-age=31536000, immutable"})

    def local_path(self, key: str) -> Path | None:
        return None


def open_store(spec: str) -> Store:
    """`r2` -> R2Store from the environment; anything else is a local directory path."""
    return R2Store() if spec == "r2" else LocalStore(Path(spec))
