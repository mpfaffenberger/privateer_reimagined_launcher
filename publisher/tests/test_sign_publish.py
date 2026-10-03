"""#23: sign-publish --run (manifest from a CI run) and --dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build, quiet
from prl import ci, cli, release
from prl.manifest import channel_key, manifest_key
from prl.store import LocalStore


def ci_upload(tmp_path, game) -> tuple[LocalStore, bytes]:
    """What the release workflow leaves behind: blobs in the CDN, an unsigned manifest."""
    dst = LocalStore(tmp_path / "cdn")
    _, local = build(tmp_path / "ci", game, None, known=dst)
    release.publish(local, dst, "1.0.0", blobs_only=True, log=quiet)
    return dst, local.get(manifest_key("1.0.0"))


def test_dry_run_checks_and_signs_but_publishes_nothing(tmp_path, game, key):
    dst, data = ci_upload(tmp_path, game)
    release.sign_publish(data, dst, key[0], dry_run=True, log=quiet)
    assert not dst.exists(manifest_key("1.0.0")) and not dst.exists(channel_key("stable"))


def test_dry_run_still_refuses_missing_blobs_and_taken_versions(tmp_path, game, key):
    dst, data = ci_upload(tmp_path, game)
    release.sign_publish(data, dst, key[0], log=quiet)            # 1.0.0 goes live
    with pytest.raises(FileExistsError):                          # different bytes, same version
        release.sign_publish(data.replace(b"2026-10-03", b"2030-01-01"), dst, key[0],
                             dry_run=True, log=quiet)
    first = sorted(dst.keys("blobs/"))[0]
    dst.local_path(first).unlink()
    with pytest.raises(ValueError, match="no matching blob"):
        release.sign_publish(data, dst, key[0], dry_run=True, log=quiet)


def test_cli_dry_run_from_a_manifest_file(tmp_path, game, key):
    dst, data = ci_upload(tmp_path, game)
    manifest = tmp_path / "1.0.0.json"
    manifest.write_bytes(data)
    rc = cli.main(["sign-publish", "--manifest", str(manifest), "--to", str(dst.root),
                   "--key", str(key[0]), "--dry-run"])
    assert rc == 0 and not dst.exists(channel_key("stable"))


class FakeGh:
    """Stands in for `gh run download`: drops `files` into the -D directory."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.argv = files, None

    def __call__(self, argv, check):
        self.argv = argv
        out = Path(argv[argv.index("-D") + 1])
        for name, data in self.files.items():
            (out / name).write_bytes(data)


def test_download_manifest_asks_gh_for_the_right_artifact():
    gh = FakeGh({"2026.10.04.1.json": b'{"format": 1}'})
    assert ci.download_manifest("12345", run=gh) == b'{"format": 1}'
    assert gh.argv[:4] == ["gh", "run", "download", "12345"]
    assert gh.argv[gh.argv.index("-R") + 1] == ci.GAME_REPO
    assert gh.argv[gh.argv.index("-n") + 1] == "release-manifest"


@pytest.mark.parametrize("files", [{}, {"a.json": b"{}", "b.json": b"{}"}])
def test_download_manifest_wants_exactly_one_manifest(files):
    with pytest.raises(ValueError, match="exactly one manifest"):
        ci.download_manifest("12345", run=FakeGh(files))


def test_download_manifest_rejects_non_numeric_run_ids():
    with pytest.raises(ValueError, match="must be a number"):
        ci.download_manifest("123; rm -rf /", run=FakeGh({}))
