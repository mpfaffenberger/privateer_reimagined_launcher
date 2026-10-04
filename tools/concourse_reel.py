"""Render a slideshow of base concourses as one seamless video loop for the site hero (#38).

Each room animates for --hold seconds (rendered by room_loop.py's renderer, so it matches the
game) and crossfades into the next over --fade seconds. Every room renders hold + fade seconds
and its last `fade` overlaps the next room; the final room's overlap wraps onto frame 0, so the
loop has no seam. Layer seams don't matter here: each room is gone before it would repeat, so
each layer just starts where it shows the most (the busiest walkers) in that window.

    uv run --with pillow --with numpy --with imageio-ffmpeg tools/concourse_reel.py \\
        --game "%GAME_REPO%" --out site/video/concourses
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from room_loop import encode, load_room, render

ROOMS = ["military", "pirate", "agricultural", "oxford", "mining", "pleasure", "refinery", "newcon"]
SIZE = (1536, 1024)
# Every room change is a full-frame crossfade plus a fresh keyframe, so a reel costs far more
# than one room's loop (6 MB at room_loop's CRF). It sits under a dark gradient and the title,
# so trade fidelity for weight. With one keyframe per loop (see room_loop.encode) that's
# ~1.2 MB WebM at 960 and ~1.7 MB at 1280 (trials in #38).
VARIANTS = [("", 1280, 44, 30), ("-960", 960, 42, 31)]


def busiest_starts(layers, window_s: float) -> None:
    """Start each layer at the tick that shows it most within the window."""
    for layer in layers:
        n = max(1, round(window_s * layer.fps))
        off = layer.meta.get("offset_frames", 0)
        shown = [not layer.blank[(t + off) % layer.period] for t in range(layer.period)]
        layer.start = max(range(layer.period), key=lambda s: sum(shown[(s + i) % layer.period] for i in range(n)))


def fit(frame: Image.Image) -> np.ndarray:
    """Cover-scale to SIZE (centre crop), as float for blending."""
    if frame.size != SIZE:
        scale = max(SIZE[0] / frame.width, SIZE[1] / frame.height)
        frame = frame.resize((round(frame.width * scale), round(frame.height * scale)), Image.Resampling.LANCZOS)
        x, y = (frame.width - SIZE[0]) // 2, (frame.height - SIZE[1]) // 2
        frame = frame.crop((x, y, x + SIZE[0], y + SIZE[1]))
    return np.asarray(frame, dtype=np.float32)


def room_frames(game: Path, base: str, fps: int, frames: range, window_s: float):
    plate, fill, stars, layers = load_room(game, f"{base}:concourse")
    busiest_starts(layers, window_s)
    return (fit(f) for f in render(plate, fill, stars, layers, fps, frames))


def reel(game: Path, rooms: list[str], fps: int, hold: float, fade: float):
    hold_n, fade_n = round(hold * fps), round(fade * fps)
    window = hold + fade

    def blended():
        # The last room's fade-out overlaps the first room's fade-in: that's the wrap.
        prev = room_frames(game, rooms[-1], fps, range(hold_n, hold_n + fade_n), window)
        for base in rooms:
            cur = room_frames(game, base, fps, range(hold_n + fade_n), window)
            for local in range(hold_n):
                frame = next(cur)
                if local < fade_n:
                    k = (local + 0.5) / fade_n
                    k = k * k * (3 - 2 * k)                           # smoothstep
                    frame = next(prev) * (1 - k) + frame * k
                yield Image.fromarray(np.clip(frame + 0.5, 0, 255).astype(np.uint8))
            prev = cur                                                # its remaining frames = the next fade

    # Rotate by the fade so frame 0 (= the poster) is the first room alone, not the wrap
    # crossfade; that crossfade now ends the video and still flows straight into frame 0.
    frames = blended()
    wrap = [next(frames) for _ in range(fade_n)]
    yield from frames
    yield from wrap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", type=Path, required=True, help="new_privateer checkout (with LFS content)")
    ap.add_argument("--out", type=Path, required=True, help="output path without extension")
    ap.add_argument("--rooms", nargs="+", default=ROOMS, help="concourse folders, in order")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--hold", type=float, default=6.0, help="seconds each room is on screen")
    ap.add_argument("--fade", type=float, default=1.5, help="crossfade seconds")
    ap.add_argument("--preview", action="store_true", help="only write a contact sheet of each room's first frame")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.preview:
        tw, th = SIZE[0] // 4, SIZE[1] // 4
        sheet = Image.new("RGB", (tw * 4, th * ((len(a.rooms) + 3) // 4)))
        for i, base in enumerate(a.rooms):
            first = next(room_frames(a.game, base, a.fps, range(1), a.hold + a.fade))
            sheet.paste(Image.fromarray(first.astype(np.uint8)).resize((tw, th)), ((i % 4) * tw, (i // 4) * th))
        sheet.save(a.out.with_name(a.out.name + "-preview.png"))
        return
    import imageio_ffmpeg
    encode(reel(a.game, a.rooms, a.fps, a.hold, a.fade), a.out, SIZE, a.fps, imageio_ffmpeg.get_ffmpeg_exe(),
           len(a.rooms) * round(a.hold * a.fps), VARIANTS)
    for p in sorted(a.out.parent.glob(a.out.name + "*")):
        print(f"  {p.name:28s} {p.stat().st_size / 1024:8.0f} KB")


if __name__ == "__main__":
    main()
