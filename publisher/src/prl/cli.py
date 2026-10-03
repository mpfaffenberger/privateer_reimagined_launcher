"""`prl` command line. See docs/DESIGN.md "Release flow"."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import release, sign
from .store import LocalStore, open_store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prl", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="create the release signing key (once; keep it offline)")
    k.add_argument("--out", type=Path, default=Path("signing_key.pem"))

    b = sub.add_parser("build", help="build a release into the local store")
    b.add_argument("--config", type=Path, required=True)
    b.add_argument("--version", required=True)
    b.add_argument("--channel", default="stable")
    b.add_argument("--store", type=Path, default=Path("dist/store"))
    b.add_argument("--key", type=Path, default=Path("signing_key.pem"))
    b.add_argument("--level", type=int, default=10, help="zstd level for new blobs")

    p = sub.add_parser("publish", help="upload a built release (missing blobs, then manifest, then pointer)")
    p.add_argument("--version", required=True)
    p.add_argument("--from", dest="src", type=Path, default=Path("dist/store"))
    p.add_argument("--to", required=True, help="'r2' or a local directory")

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
        release.build(a.config, a.version, a.channel, LocalStore(a.store), a.key, level=a.level)
    elif a.cmd == "publish":
        release.publish(LocalStore(a.src), open_store(a.to), a.version)
    elif a.cmd == "verify":
        problems = release.verify(open_store(a.store), a.channel, a.pubkey, deep=a.deep)
        for msg in problems:
            print("  -", msg)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
