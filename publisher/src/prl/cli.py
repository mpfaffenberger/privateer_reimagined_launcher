"""`prl` command line. See docs/DESIGN.md "Release flow"."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import cdn_files, ci, config, launcher_dist, release, sign
from .store import LocalStore, open_store

# Where the release signing key lives on the publishing machine (docs/DESIGN.md).
DEFAULT_KEY = Path.home() / ".prl" / "signing_key.pem"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prl", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="create the release signing key (once; keep it offline)")
    k.add_argument("--out", type=Path, default=DEFAULT_KEY)

    b = sub.add_parser("build", help="build a release into the local store")
    b.add_argument("--config", type=Path, required=True)
    b.add_argument("--version", required=True)
    b.add_argument("--channel", default="stable")
    b.add_argument("--os", dest="os_name", default="windows", choices=config.OSES,
                   help="which OS the release is for; non-Windows channels end in -<os> (e.g. stable-macos)")
    b.add_argument("--store", type=Path, default=Path("dist/store"))
    signing = b.add_mutually_exclusive_group()
    signing.add_argument("--key", type=Path, default=DEFAULT_KEY)
    signing.add_argument("--unsigned", action="store_true",
                         help="leave the manifest unsigned (CI); sign later with sign-publish")
    b.add_argument("--level", type=int, default=10, help="zstd level for new blobs")
    b.add_argument("--lfs-pointers", action="store_true",
                   help="sources may be Git LFS pointers; pull only content nobody has yet")
    b.add_argument("--known-store", help="'r2' or a directory whose blobs count as already built")

    p = sub.add_parser("publish", help="upload a built release (missing blobs, then manifest, then pointer)")
    p.add_argument("--version", required=True)
    p.add_argument("--from", dest="src", type=Path, default=Path("dist/store"))
    p.add_argument("--to", required=True, help="'r2' or a local directory")
    p.add_argument("--blobs-only", action="store_true",
                   help="upload content only; the release goes live with sign-publish")

    sp = sub.add_parser("sign-publish", help="sign an unsigned manifest and make it live "
                                             "(checks every blob is already uploaded)")
    source = sp.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", help="game-repo release workflow run id (manifest fetched via gh)")
    source.add_argument("--manifest", type=Path, help="unsigned manifest JSON file")
    sp.add_argument("--repo", default=ci.GAME_REPO, help="repo the --run belongs to")
    sp.add_argument("--to", required=True, help="'r2' or a local directory")
    sp.add_argument("--key", type=Path, default=DEFAULT_KEY)
    sp.add_argument("--dry-run", action="store_true",
                    help="run every check and sign, but don't publish")

    v = sub.add_parser("verify", help="check a published channel like the launcher does")
    v.add_argument("--store", required=True, help="'r2' or a local directory")
    v.add_argument("--channel", default="stable")
    v.add_argument("--pubkey", required=True, help="base64 ed25519 public key")
    v.add_argument("--deep", action="store_true", help="also download + hash every blob")

    u = sub.add_parser("upload-launcher",
                       help="publish the launcher (Windows and/or macOS) + signatures, then point the update feed at it")
    u.add_argument("--installer", type=Path, help="Windows: the NSIS setup .exe (its .sig beside it)")
    u.add_argument("--macos-archive", type=Path, help="macOS: the .app.tar.gz the updater fetches (its .sig beside it)")
    u.add_argument("--macos-dmg", type=Path, help="macOS: the .dmg people download from the site")
    u.add_argument("--version", required=True, help="launcher version, e.g. 0.1.0")
    u.add_argument("--notes", default="", help="release notes shown in the launcher's update prompt")
    u.add_argument("--to", required=True, help="'r2' or a local directory")

    f = sub.add_parser("upload-file", help="publish files under content-hashed names (e.g. site video)")
    f.add_argument("files", type=Path, nargs="+")
    f.add_argument("--prefix", required=True, help="key prefix, e.g. site/video")
    f.add_argument("--to", required=True, help="'r2' or a local directory")

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        pub = sign.keygen(a.out)
        print(f"wrote {a.out}  (keep it OFFLINE and backed up; never commit it)")
        print(f"public key (paste into crates/prl-core/src/keys.rs): {pub}")
    elif a.cmd == "build":
        release.build(a.config, a.version, a.channel, LocalStore(a.store),
                      None if a.unsigned else a.key, level=a.level,
                      known=open_store(a.known_store) if a.known_store else None,
                      lfs_pointers=a.lfs_pointers, os_name=a.os_name)
    elif a.cmd == "publish":
        release.publish(LocalStore(a.src), open_store(a.to), a.version, blobs_only=a.blobs_only)
    elif a.cmd == "sign-publish":
        data = ci.download_manifest(a.run, a.repo) if a.run else a.manifest.read_bytes()
        release.sign_publish(data, open_store(a.to), a.key, dry_run=a.dry_run)
    elif a.cmd == "upload-launcher":
        if not a.installer and not a.macos_archive:
            ap.error("upload-launcher needs --installer and/or --macos-archive + --macos-dmg")
        if bool(a.macos_archive) != bool(a.macos_dmg):
            ap.error("--macos-archive and --macos-dmg go together")
        store = open_store(a.to)
        if a.installer:
            launcher_dist.upload_installer(store, a.installer, a.version, a.notes)
        if a.macos_archive:
            launcher_dist.upload_macos(store, a.macos_archive, a.macos_dmg, a.version, a.notes)
    elif a.cmd == "upload-file":
        store = open_store(a.to)
        for path in a.files:
            cdn_files.upload_file(store, path, a.prefix)
    elif a.cmd == "verify":
        problems = release.verify(open_store(a.store), a.channel, a.pubkey, deep=a.deep)
        for msg in problems:
            print("  -", msg)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
