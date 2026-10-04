"""Check the website's download button against the SHA-256 it prints (#62).

Downloads whatever the button links to and compares it with the hash in the page's
"To check your download" line, so the two can't drift apart when the button moves to a new
launcher version. Run it after pointing the button at a new installer:

    python tools/check_site_download.py            # site/index.html in this checkout
    python tools/check_site_download.py --live     # https://privateer-reimagined.com/
"""

from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

LIVE = "https://privateer-reimagined.com/"
PAGE = Path(__file__).resolve().parent.parent / "site" / "index.html"
BUTTON = re.compile(r'href="(https://cdn\.privateer-reimagined\.com/launcher/PrivateerReimagined-Setup-([0-9.]+)-x64\.exe)"')
HASH = re.compile(r'class="hash"[^<]*<code>([0-9a-f]{64})</code>')


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "prl-check"})) as r:
        return r.read()


def main(argv: list[str]) -> int:
    html = fetch(f"{LIVE}?check").decode() if "--live" in argv else PAGE.read_text(encoding="utf-8")
    buttons = set(BUTTON.findall(html))
    hashes = set(HASH.findall(html))
    if len(buttons) != 1 or len(hashes) != 1:
        print(f"expected one installer link and one hash, found {len(buttons)} and {len(hashes)}")
        return 1
    (url, version), (printed,) = buttons.pop(), hashes
    if f"Version {version} " not in html:
        print(f"the note under the button doesn't say 'Version {version}'")
        return 1
    actual = hashlib.sha256(fetch(url)).hexdigest()
    ok = actual == printed
    print(f"{'OK' if ok else 'MISMATCH'}: {version} -> {url}\n  page   {printed}\n  actual {actual}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
