"""Check the website's download buttons against the SHA-256s it prints (#62, #69).

Each button and its hash carry the same data-platform (windows, mac). For every platform this
downloads what the button links to and compares it with the printed hash, so they can't drift
apart when a button moves to a new launcher version. Run it after pointing the buttons at new
files:

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
BUTTON = re.compile(r'<a class="button" data-platform="(\w+)" href="(https://cdn\.privateer-reimagined\.com/launcher/'
                    r'PrivateerReimagined-(?:Setup-)?([0-9.]+)-[\w.-]+)">')
HASH = re.compile(r'<code data-platform="(\w+)">([^<]*)</code>')


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "prl-check"})) as r:
        return r.read()


def main(argv: list[str]) -> int:
    html = fetch(f"{LIVE}?check").decode() if "--live" in argv else PAGE.read_text(encoding="utf-8")
    buttons = {p: (url, v) for p, url, v in BUTTON.findall(html)}
    hashes = dict(HASH.findall(html))
    if not buttons or set(buttons) != set(hashes):
        print(f"each download button needs a printed hash: buttons {sorted(buttons)}, hashes {sorted(hashes)}")
        return 1
    versions = {v for _, v in buttons.values()}
    if len(versions) != 1 or f"Version {versions.pop()} " not in html:
        print(f"the buttons and the 'Version ...' note disagree: {buttons}")
        return 1
    ok = True
    for platform, (url, version) in sorted(buttons.items()):
        printed = hashes[platform]
        actual = hashlib.sha256(fetch(url)).hexdigest()
        good = printed == actual
        ok &= good
        print(f"{'OK' if good else 'MISMATCH'}: {platform} {version} -> {url}\n  page   {printed}\n  actual {actual}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
