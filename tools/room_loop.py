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


def render(plate, fill, stars, layers, fps_out: int, frames: range, loop_seconds: int | None = None):
    """Yield RGB frames `frames` (output-frame numbers). With `loop_seconds`, the sky blends
    back to its start over the loop's last SKY_XFADE_S so the loop is seamless."""
    w, h = plate.size
    sky = SkyRenderer(fill, stars, w, h) if fill is not None else None
    for n in frames:
        t = n / fps_out
        if sky:
            rgb = sky.at(t)
            fade_from = loop_seconds - SKY_XFADE_S if loop_seconds else math.inf
            if t > fade_from:                         # blend toward where the loop restarts
                k = (t - fade_from) / SKY_XFADE_S
                rgb = rgb * (1 - k) + sky.at(t - loop_seconds) * k
            canvas = Image.fromarray((rgb * 255).astype(np.uint8)).convert("RGBA")
        else:
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        draw_layers(canvas, layers, n, fps_out, under=True)
        canvas.alpha_composite(plate)
        draw_layers(canvas, layers, n, fps_out, under=False)
        yield canvas.convert("RGB")


# (name suffix, output width or None for native, VP9 CRF, H.264 CRF)
FULL_SIZE = [("", None, 36, 25)]
# x264 starves the last frames of a stream (nothing later references them), so a loop's final
# frame pops against its keyframe at the wrap. Encode this many frames past the end (the loop's
# start again), with no B-frames so decode order is display order, then stream-copy them off.
X264_PAD = 24


def encode(frames, out: Path, size: tuple[int, int], fps_out: int, ffmpeg: str, loop_frames: int,
           variants=FULL_SIZE):
    """Stream `loop_frames` frames once into a VP9 WebM and an H.264 MP4 (for older Safari) per
    variant, each with one keyframe per loop: periodic keyframes pulse visibly at high CRF, and
    a mid-loop one buys nothing for a video that only ever restarts at frame 0. The poster is
    frame 0 at native size."""
    w, h = size
    common = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
              "-s", f"{w}x{h}", "-r", str(fps_out), "-i", "-", "-an", "-pix_fmt", "yuv420p"]
    vp9, x264, padded = [], [], []
    for suffix, width, vp9_crf, x264_crf in variants:
        scale = ["-vf", f"scale={width}:-2:flags=lanczos"] if width else []
        base = out.with_name(out.name + suffix)
        vp9.append(subprocess.Popen(
            common + scale + ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(vp9_crf), "-g", str(loop_frames),
                              "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", str(base.with_suffix(".webm"))],
            stdin=subprocess.PIPE))
        tmp = base.with_name(base.name + ".padded.mp4")
        padded.append((tmp, base.with_suffix(".mp4")))
        x264.append(subprocess.Popen(
            common + scale + ["-c:v", "libx264", "-crf", str(x264_crf), "-preset", "slow",
                              "-g", str(loop_frames + X264_PAD), "-x264-params", "scenecut=0:bframes=0", str(tmp)],
            stdin=subprocess.PIPE))
    poster, head, count = None, [], 0
    for frame in frames:
        data = frame.tobytes()
        if count == 0:
            poster = frame
        if count < X264_PAD:
            head.append(data)
        for p in vp9 + x264:
            p.stdin.write(data)
        count += 1
    if count != loop_frames:
        raise SystemExit(f"expected {loop_frames} frames, got {count}")
    for data in head:                                 # x264 only: the loop's start again
        for p in x264:
            p.stdin.write(data)
    for p in vp9 + x264:
        p.stdin.close()
        if p.wait():
            raise SystemExit("ffmpeg failed")
    for tmp, final in padded:                         # cut the padding off, no re-encode
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(tmp), "-c", "copy", "-frames:v",
                        str(loop_frames), "-movflags", "+faststart", str(final)], check=True)
        tmp.unlink()
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
        for n, frame in enumerate(render(plate, fill, stars, layers, a.fps, range(total), seconds)):
            if n in wanted:
                frame.save(f"{a.out}-{wanted[n]}.png")
        return
    import imageio_ffmpeg
    encode(render(plate, fill, stars, layers, a.fps, range(seconds * a.fps), seconds), a.out, plate.size,
           a.fps, imageio_ffmpeg.get_ffmpeg_exe(), seconds * a.fps)
    for ext in (".webm", ".mp4", ".webp"):
        p = a.out.with_suffix(ext)
        print(f"  {p.name:28s} {p.stat().st_size / 1024:8.0f} KB")


if __name__ == "__main__":
    main()
