"""Render a game room's animation to a seamless video loop for the website (#32).

Mirrors the game's renderer (new_privateer src/room_anim.cpp + room_anim_data.cpp):
  slot  = (floor(seconds * fps) + offset_frames) mod period_frames; empty slots draw nothing
  order = sky fill, scrolling star tiles, `under` layers, the plate (sky mask as alpha),
          then the other layers in JSON order; frames drawn at dst scaled canvas -> plate.

Layer periods are deliberately coprime in the game, so a room almost never repeats. A web
loop instead starts each layer at its own point so that every seam falls in a blank gap
(a walker is offstage) or on a whole number of periods (a lantern). The starfield can't
loop in a few seconds, so only the sky crossfades into its start over the last second.

    uv run --with pillow --with numpy --with imageio-ffmpeg tools/room_loop.py \
        --game "%GAME_REPO%" --room newdetroit:concourse --out site/video/new-detroit
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# Blank time required on both sides of a layer's seam. Long enough that a seam can't land in
# a short gap inside one walk (a walker briefly behind a pillar), which would cut it in half.
SEAM_GUARD_S = 1.5
SKY_XFADE_S = 1.0


@dataclass
class Layer:
    name: str
    atlas: Image.Image
    meta: dict
    period: int
    fps: float
    blank: list[bool]       # per slot
    start: int = 0          # tick the loop begins at (planned)

    def frame(self, tick: int) -> dict | None:
        slot = (tick + self.meta.get("offset_frames", 0)) % self.period
        return self.by_slot.get(slot)

    def __post_init__(self):
        self.by_slot = {f["slot"]: f for f in self.meta["frames"]}


@dataclass
class Stars:
    tile: np.ndarray        # H x W x 4 float
    velocity: tuple[float, float]
    spin: float


def load_room(game: Path, spec: str):
    base, room_name = spec.split(":")
    cfg_dir = game / "assets" / "concourse" / base
    room = json.loads((cfg_dir / "concourse.json").read_text(encoding="utf-8"))["rooms"][room_name]
    plate = Image.open(cfg_dir / room["background"]).convert("RGBA")
    sky = room.get("sky")
    fill, stars = None, []
    if sky:
        mask = Image.open(cfg_dir / sky["mask"]).convert("L")
        plate.putalpha(Image.eval(mask, lambda m: 255 - m))      # mask 255 = sky = see-through
        fill = Image.open(cfg_dir / sky["fill"]).convert("RGBA").resize(plate.size)
        for s in sky.get("stars", []):
            tile = np.asarray(Image.open(cfg_dir / s["tile"]).convert("RGBA"), dtype=np.float32) / 255
            stars.append(Stars(tile, tuple(s["velocity"]), float(s.get("spin", 0.0))))
    layers = []
    for rel in room.get("layers", []):
        path = cfg_dir / rel
        meta = json.loads(path.read_text(encoding="utf-8"))
        filled = {f["slot"] for f in meta["frames"]}
        period = meta["period_frames"]
        layers.append(Layer(path.stem, Image.open(path.parent / meta["atlas"]).convert("RGBA"), meta,
                            period, float(meta["fps"]), [s not in filled for s in range(period)]))
    return plate, fill, stars, layers


def seam_start(layer: Layer, seconds: int) -> int | None:
    """A start tick whose loop of `seconds` is seamless for this layer, or None."""
    n = round(seconds * layer.fps)
    if n % layer.period == 0:
        return 0                                    # whole periods: always seamless
    guard = max(1, round(SEAM_GUARD_S * layer.fps))
    off = layer.meta.get("offset_frames", 0)
    blank = lambda t: layer.blank[(t + off) % layer.period]
    for s in range(layer.period):
        if all(blank(s + i) for i in range(guard)) and all(blank(s + n - 1 - i) for i in range(guard)) \
                and any(not blank(s + i) for i in range(n)):   # and it actually shows something
            return s
    return None


def plan(layers: list[Layer], lo: int, hi: int) -> int:
    for seconds in range(lo, hi + 1):
        starts = [seam_start(layer, seconds) for layer in layers]
        if all(s is not None for s in starts):
            for layer, s in zip(layers, starts):
                layer.start = s
            return seconds
    raise SystemExit(f"no seamless loop between {lo} and {hi} s for this room")


class SkyRenderer:
    def __init__(self, fill: Image.Image, stars: list[Stars], w: int, h: int):
        self.fill = np.asarray(fill, dtype=np.float32)[..., :3] / 255
        self.stars = stars
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        self.cx, self.cy = w * 0.5, h * 0.5                       # rooms have no anchor
        self.dx, self.dy = xs - self.cx, ys - self.cy

    def at(self, seconds: float) -> np.ndarray:
        out = self.fill.copy()
        for s in self.stars:
            th, tw = s.tile.shape[:2]
            a = math.radians(math.fmod(s.spin * seconds, 360.0))
            cs, sn = math.cos(a), math.sin(a)
            u = (self.cx + cs * self.dx + sn * self.dy) - math.fmod(s.velocity[0] * seconds, tw)
            v = (self.cy - sn * self.dx + cs * self.dy) - math.fmod(s.velocity[1] * seconds, th)
            texel = s.tile[np.mod(v, th).astype(np.int32), np.mod(u, tw).astype(np.int32)]
            alpha = texel[..., 3:4]
            out = out * (1 - alpha) + texel[..., :3] * alpha
        return out


def draw_layers(canvas: Image.Image, layers: list[Layer], frame_no: int, fps_out: int, under: bool):
    for layer in layers:
        if bool(layer.meta.get("under")) != under:
            continue
        tick = layer.start + (frame_no * round(layer.fps)) // fps_out
        f = layer.frame(tick)
        if not f:
            continue
        sx = canvas.width / layer.meta["canvas"][0]
        sy = canvas.height / layer.meta["canvas"][1]
        x, y, w, h = f["src"]
        sprite = layer.atlas.crop((x, y, x + w, y + h))
        dx, dy, dw, dh = f["dst"]
        size = (round(dw * sx), round(dh * sy))
        if size != sprite.size:
            sprite = sprite.resize(size, Image.Resampling.BILINEAR)
        canvas.alpha_composite(sprite, (round(dx * sx), round(dy * sy)))


def render(plate, fill, stars, layers, seconds: int, fps_out: int):
    w, h = plate.size
    sky = SkyRenderer(fill, stars, w, h) if fill is not None else None
    total = seconds * fps_out
    for n in range(total):
        t = n / fps_out
        if sky:
            rgb = sky.at(t)
            fade_from = seconds - SKY_XFADE_S
            if t > fade_from:                         # blend toward where the loop restarts
                k = (t - fade_from) / SKY_XFADE_S
                rgb = rgb * (1 - k) + sky.at(t - seconds) * k
            canvas = Image.fromarray((rgb * 255).astype(np.uint8)).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        draw_layers(canvas, layers, n, fps_out, under=True)
        canvas.alpha_composite(plate)
        draw_layers(canvas, layers, n, fps_out, under=False)
        yield canvas.convert("RGB")


def encode(frames, out: Path, size: tuple[int, int], fps_out: int, ffmpeg: str):
    """Stream frames once into two encoders: VP9 WebM and H.264 MP4 (for older Safari)."""
    w, h = size
    common = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
              "-s", f"{w}x{h}", "-r", str(fps_out), "-i", "-", "-an", "-pix_fmt", "yuv420p"]
    procs = [
        subprocess.Popen(common + ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36", "-row-mt", "1",
                                   "-deadline", "good", "-cpu-used", "2", str(out.with_suffix(".webm"))],
                         stdin=subprocess.PIPE),
        subprocess.Popen(common + ["-c:v", "libx264", "-crf", "25", "-preset", "slow",
                                   "-movflags", "+faststart", str(out.with_suffix(".mp4"))],
                         stdin=subprocess.PIPE),
    ]
    poster = None
    for i, frame in enumerate(frames):
        if i == 0:
            poster = frame
        data = frame.tobytes()
        for p in procs:
            p.stdin.write(data)
    for p in procs:
        p.stdin.close()
        if p.wait():
            raise SystemExit("ffmpeg failed")
    poster.save(out.with_suffix(".webp"), "WEBP", quality=74, method=6)   # = frame 0, no jump


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", type=Path, required=True, help="new_privateer checkout (with LFS content)")
    ap.add_argument("--room", required=True, help="base:room, e.g. newdetroit:concourse")
    ap.add_argument("--out", type=Path, required=True, help="output path without extension")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--min-seconds", type=int, default=20)
    ap.add_argument("--max-seconds", type=int, default=60)
    ap.add_argument("--stills", type=int, default=0, help="only write N evenly spaced stills (preview)")
    a = ap.parse_args()

    plate, fill, stars, layers = load_room(a.game, a.room)
    seconds = plan(layers, a.min_seconds, a.max_seconds)
    print(f"{a.room}: {seconds} s loop, " + ", ".join(f"{l.name}@{l.start}" for l in layers))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.stills:                                   # stream; keep only the frames asked for
        total = seconds * a.fps
        wanted = {k * total // a.stills: f"still{k}" for k in range(a.stills)} | {total - 1: "last"}
        for n, frame in enumerate(render(plate, fill, stars, layers, seconds, a.fps)):
            if n in wanted:
                frame.save(f"{a.out}-{wanted[n]}.png")
        return
    import imageio_ffmpeg
    encode(render(plate, fill, stars, layers, seconds, a.fps), a.out, plate.size, a.fps,
           imageio_ffmpeg.get_ffmpeg_exe())
    for ext in (".webm", ".mp4", ".webp"):
        p = a.out.with_suffix(ext)
        print(f"  {p.name:28s} {p.stat().st_size / 1024:8.0f} KB")


if __name__ == "__main__":
    main()
