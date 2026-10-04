"""#34: content-hashed, write-once uploads (site video)."""

from __future__ import annotations

import hashlib

import pytest

from conftest import quiet
from prl import cdn_files, cli
from prl.store import LocalStore, content_type


def clip(tmp_path, data=b"\x1aE\xdf\xa3 webm bytes", name="new-detroit.webm"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_name_carries_the_content_hash(tmp_path):
    store = LocalStore(tmp_path / "cdn")
    url = cdn_files.upload_file(store, clip(tmp_path), "site/video", log=quiet)
    sha10 = hashlib.sha256(clip(tmp_path).read_bytes()).hexdigest()[:10]
    key = f"site/video/new-detroit-{sha10}.webm"
    assert url == f"{cdn_files.CDN}/{key}" and store.get(key) == clip(tmp_path).read_bytes()


def test_changed_bytes_get_a_new_name_and_the_old_one_stays(tmp_path):
    store = LocalStore(tmp_path / "cdn")
    first = cdn_files.upload_file(store, clip(tmp_path), "site/video", log=quiet)
    second = cdn_files.upload_file(store, clip(tmp_path, b"re-rendered"), "site/video", log=quiet)
    assert first != second and len(store.keys("site/video")) == 2


def test_publish_immutable_refuses_different_bytes(tmp_path):
    store = LocalStore(tmp_path / "cdn")
    cdn_files.publish_immutable(store, "a/b.bin", clip(tmp_path, b"one", "b.bin"), log=quiet)
    cdn_files.publish_immutable(store, "a/b.bin", clip(tmp_path, b"one", "b.bin"), log=quiet)
    with pytest.raises(FileExistsError):
        cdn_files.publish_immutable(store, "a/b.bin", clip(tmp_path, b"two", "b.bin"), log=quiet)


@pytest.mark.parametrize("prefix", ["../x", "site/../etc", "Site/Video", "", "a b"])
def test_bad_prefixes_are_rejected(tmp_path, prefix):
    with pytest.raises(ValueError, match="bad prefix"):
        cdn_files.upload_file(LocalStore(tmp_path), clip(tmp_path), prefix, log=quiet)


def test_cli_uploads_several(tmp_path):
    a, b = clip(tmp_path, b"a", "a.webm"), clip(tmp_path, b"b", "b.mp4")
    assert cli.main(["upload-file", str(a), str(b), "--prefix", "site/video", "--to", str(tmp_path / "cdn")]) == 0
    assert len(LocalStore(tmp_path / "cdn").keys("site/video")) == 2


def test_video_content_types():
    assert content_type("site/video/x-0123456789.webm") == "video/webm"
    assert content_type("site/video/x-0123456789.mp4") == "video/mp4"
