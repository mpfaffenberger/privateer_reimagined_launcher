"""Fetch the unsigned manifest a game-repo release run produced (#23, game #760)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

GAME_REPO = "mpfaffenberger/privateer_reimagined"
ARTIFACT = "release-manifest"     # must match .github/workflows/release.yml in the game repo


def download_manifest(run_id: str, repo: str = GAME_REPO, run=subprocess.run) -> bytes:
    """The manifest JSON from run `run_id`'s release-manifest artifact (via `gh`)."""
    if not run_id.isdigit():
        raise ValueError(f"run id must be a number, got {run_id!r}")
    with tempfile.TemporaryDirectory() as tmp:
        run(["gh", "run", "download", run_id, "-R", repo, "-n", ARTIFACT, "-D", tmp], check=True)
        found = sorted(Path(tmp).rglob("*.json"))
        if len(found) != 1:
            raise ValueError(f"expected exactly one manifest in run {run_id}'s {ARTIFACT} "
                             f"artifact, found {len(found)}")
        return found[0].read_bytes()
