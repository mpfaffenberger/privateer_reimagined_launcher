"""`prl` command line. See docs/DESIGN.md "Release flow"."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import release, sign
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
    sp.add_argument("--manifest", type=Path, required=True, help="unsigned manifest JSON (e.g. from CI)")
    sp.add_argument("--to", required=True, help="'r2' or a local directory")
    sp.add_argument("--key", type=Path, default=DEFAULT_KEY)

    v = sub.add_parser("verify", help="check a published channel like the launcher does")
    v.add_argument("--store", required=True, help="'r2' or a local directory")
    v.add_argument("--channel", default="stable")
    v.add_argument("--pubkey", required=True, help="base64 ed25519 public key")
    v.add_argument("--deep", action="store_true", help="also download + hash every blob")

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        pub = sign.keygen(a.out)
        print(f"wrote {a.out}  (keep it OFFLINE and backed up; never commit it)")
        print(f"public key (paste into crates/prl-core/src/keys.rs): {pub}")
    elif a.cmd == "build":
        release.build(a.config, a.version, a.channel, LocalStore(a.store),
                      None if a.unsigned else a.key, level=a.level,
                      known=open_store(a.known_store) if a.known_store else None,
                      lfs_pointers=a.lfs_pointers)
    elif a.cmd == "publish":
        release.publish(LocalStore(a.src), open_store(a.to), a.version, blobs_only=a.blobs_only)
    elif a.cmd == "sign-publish":
        release.sign_publish(a.manifest.read_bytes(), open_store(a.to), a.key)
    elif a.cmd == "verify":
        problems = release.verify(open_store(a.store), a.channel, a.pubkey, deep=a.deep)
        for msg in problems:
            print("  -", msg)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
