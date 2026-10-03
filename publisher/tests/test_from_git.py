"""#21: build from Git LFS pointers, pull only missing content, sign elsewhere."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from conftest import CREATED, build, quiet
from prl import lfs, release
from prl.manifest import Manifest, channel_key, manifest_key
from prl.store import LocalStore


def to_pointer(path: Path) -> bytes:
    """Replace `path` with its LFS pointer stub; return the real content."""
    data = path.read_bytes()
    path.write_bytes(b"version https://git-lfs.github.com/spec/v1\n"
                     b"oid sha256:" + hashlib.sha256(data).hexdigest().encode() + b"\n"
                     b"size " + str(len(data)).encode() + b"\n")
    return data


def pointerize_assets(game: Path) -> dict[Path, bytes]:
    return {p: to_pointer(p) for p in sorted((game / "assets").rglob("*")) if p.is_file()}


class FakePull:
    """Stands in for `git lfs pull`: restores real content and records what was asked for."""

    def __init__(self, real: dict[Path, bytes], tamper: bool = False):
        self.real, self.tamper, self.requested = real, tamper, []

    def __call__(self, paths, log=quiet):
        self.requested.extend(paths)
        for p in paths:
            p.write_bytes(b"tampered" if self.tamper else self.real[p])


def refuse_pull(paths, log=quiet):
    raise AssertionError(f"nothing should be pulled, asked for {paths}")


def published(tmp_path, game, key) -> LocalStore:
    """A destination (stand-in for R2) holding a fully built + published v1.0.0."""
    _, local = build(tmp_path / "v1", game, key)
    dst = LocalStore(tmp_path / "cdn")
    release.publish(local, dst, "1.0.0", log=quiet)
    return dst


# ---- manifests from pointers -------------------------------------------------

def test_manifest_from_pointers_is_byte_identical_and_pulls_nothing(tmp_path, game, key):
    dst = published(tmp_path, game, key)
    pointerize_assets(game)
    m, _ = build(tmp_path / "ci", game, None, known=dst, lfs_pointers=True, pull=refuse_pull)
    assert m.to_bytes() == dst.get(manifest_key("1.0.0"))


def test_only_content_the_destination_lacks_is_pulled_and_built(tmp_path, game, key):
    dst = published(tmp_path, game, key)
    (game / "assets" / "data" / "new_mission.json").write_text('{"new": true}\n' * 50)
    puller = FakePull(pointerize_assets(game))
    m, local = build(tmp_path / "ci", game, None, version="1.1.0", known=dst,
                     lfs_pointers=True, pull=puller)
    assert [p.name for p in puller.requested] == ["new_mission.json"]
    new = next(f for f in m.files if f.path.endswith("new_mission.json"))
    assert local.exists(new.blob_key()) and not dst.exists(new.blob_key())
    assert len(local.keys("blobs/")) == 1        # nothing already in the destination was rebuilt


def test_pulled_content_must_match_its_pointer(tmp_path, game, key):
    dst = published(tmp_path, game, key)
    (game / "assets" / "data" / "new_mission.json").write_text("fresh\n")
    with pytest.raises(ValueError, match="doesn't match its pointer"):
        build(tmp_path / "ci", game, None, version="1.1.0", known=dst, lfs_pointers=True,
              pull=FakePull(pointerize_assets(game), tamper=True))


# ---- CI uploads blobs, Mike signs ------------------------------------------------

def test_unsigned_build_then_blobs_only_then_sign_publish(tmp_path, game, key):
    dst = LocalStore(tmp_path / "cdn")
    m, local = build(tmp_path / "ci", game, None, known=dst)
    mkey = manifest_key("1.0.0")
    assert local.exists(mkey) and not local.exists(mkey + ".sig")

    release.publish(local, dst, "1.0.0", blobs_only=True, log=quiet)
    assert dst.keys("blobs/") and not dst.exists(mkey) and not dst.exists(channel_key("stable"))

    release.sign_publish(local.get(mkey), dst, key[0], log=quiet)
    assert release.verify(dst, "stable", key[1], deep=True, log=quiet) == []


def test_sign_publish_refuses_while_any_blob_is_missing_or_wrong(tmp_path, game, key):
    dst = LocalStore(tmp_path / "cdn")
    m, local = build(tmp_path / "ci", game, None, known=dst)
    release.publish(local, dst, "1.0.0", blobs_only=True, log=quiet)
    victim = dst.local_path(m.files[0].blob_key())
    victim.write_bytes(victim.read_bytes() + b"!")              # wrong size
    with pytest.raises(ValueError, match="no matching blob"):
        release.sign_publish(local.get(manifest_key("1.0.0")), dst, key[0], log=quiet)
    victim.unlink()                                             # missing
    with pytest.raises(ValueError, match="no matching blob"):
        release.sign_publish(local.get(manifest_key("1.0.0")), dst, key[0], log=quiet)
    assert not dst.exists(channel_key("stable"))


def test_publish_fails_loudly_when_a_blob_is_nowhere(tmp_path, game, key):
    m, local = build(tmp_path / "ci", game, key)
    local.local_path(m.files[0].blob_key()).unlink()
    with pytest.raises(FileNotFoundError, match="neither the build store nor the destination"):
        release.publish(local, LocalStore(tmp_path / "cdn"), "1.0.0", log=quiet)


def test_a_published_version_is_never_overwritten(tmp_path, game, key):
    dst = published(tmp_path, game, key)
    (game / "new_privateer.exe").write_bytes(b"MZ different")
    with pytest.raises(FileExistsError):       # caught at build time, via the destination
        build(tmp_path / "again", game, None, known=dst)
    other = Manifest.from_bytes(dst.get(manifest_key("1.0.0")))
    other.created_utc = "2030-01-01T00:00:00Z"
    with pytest.raises(FileExistsError):       # and at publish time
        release.finalize(dst, other.to_bytes(), b"sig", log=quiet)


def test_verify_flags_a_truncated_blob(tmp_path, game, key):
    dst = published(tmp_path, game, key)
    m = Manifest.from_bytes(dst.get(manifest_key("1.0.0")))
    blob = dst.local_path(m.files[0].blob_key())
    blob.write_bytes(blob.read_bytes()[:-1])
    assert any("wrong size" in p for p in release.verify(dst, "stable", key[1], log=quiet))


# ---- lfs helpers ------------------------------------------------------------------

def test_read_pointer(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG real")
    assert lfs.read_pointer(f) is None
    real = to_pointer(f)
    assert lfs.read_pointer(f) == (hashlib.sha256(real).hexdigest(), len(real))
    f.write_bytes(b"version https://git-lfs.github.com/spec/v1\nsize 12\n")
    with pytest.raises(ValueError, match="malformed"):
        lfs.read_pointer(f)


def test_batches_cover_everything_within_the_limit():
    items = [f"assets/file_{i:04d}.png" for i in range(1000)]
    batches = lfs._batches(items, 500)
    assert [x for b in batches for x in b] == items
    assert all(len(",".join(b)) <= 500 for b in batches) and len(batches) > 1


def test_pull_refuses_paths_git_lfs_would_treat_as_patterns(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    odd = tmp_path / "a,b.png"
    odd.write_bytes(b"x")
    with pytest.raises(ValueError, match="literally"):
        lfs.pull([odd], log=quiet)
