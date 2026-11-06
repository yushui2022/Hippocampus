"""Turn a green-screen mp4 into a transparent VP9 webm (alpha), normalized to a fixed canvas.

Usage:
    python h3_to_webm.py <in.mp4> <out_dir> [--name idle] [--height 360]

Pipeline: HSV hue keying (only green hues, so blue hair stays) -> spill removal ->
per-clip union bbox -> fixed crop -> scale to target canvas -> VP9 yuva420p webm.
"""

from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HUE_MIN, HUE_MAX = 70.0, 170.0
SAT_MIN, VAL_MIN = 0.15, 0.15
FEATHER = 8.0


def read_frames(path: Path) -> tuple[list[np.ndarray], float]:
    frames: list[np.ndarray] = []
    container = av.open(str(path))
    stream = container.streams.video[0]
    fps = float(stream.average_rate or 24)
    for frame in container.decode(video=0):
        frames.append(frame.to_ndarray(format="rgb24"))
    container.close()
    return frames, fps


def rgb_to_hsv(arr: np.ndarray) -> np.ndarray:
    rgb = arr.astype(np.float32) / 255.0
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    diff = mx - mn
    hue = np.zeros_like(mx)
    nz = diff > 1e-6
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    idx = nz & (mx == r)
    hue[idx] = (60 * ((g[idx] - b[idx]) / diff[idx]) + 360) % 360
    idx = nz & (mx == g)
    hue[idx] = 60 * ((b[idx] - r[idx]) / diff[idx]) + 120
    idx = nz & (mx == b)
    hue[idx] = 60 * ((r[idx] - g[idx]) / diff[idx]) + 240
    sat = np.where(mx > 1e-6, diff / np.maximum(mx, 1e-6), 0.0)
    return np.stack([hue, sat, mx], axis=-1)


def key_frame(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (rgb with spill removed, alpha 0..255)."""
    hsv = rgb_to_hsv(rgb)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # distance inside the green hue band, feathered at both edges
    inner = (hue > HUE_MIN + FEATHER) & (hue < HUE_MAX - FEATHER)
    greenness = np.zeros_like(hue)
    greenness[inner] = 1.0
    lo = (hue >= HUE_MIN) & (hue <= HUE_MIN + FEATHER)
    hi = (hue <= HUE_MAX) & (hue >= HUE_MAX - FEATHER)
    greenness[lo] = (hue[lo] - HUE_MIN) / FEATHER
    greenness[hi] = (HUE_MAX - hue[hi]) / FEATHER
    greenness = np.clip(greenness, 0.0, 1.0)

    solid = (sat >= SAT_MIN) & (val >= VAL_MIN)
    alpha = np.where(solid, 1.0 - greenness, 1.0)
    alpha = np.clip(alpha, 0.0, 1.0)

    # remove green spill from every pixel that still carries a green cast
    out = rgb.astype(np.float32).copy()
    spill = (alpha > 0.02) & (greenness > 0.12) & (sat > 0.10)
    if spill.any():
        g = out[..., 1]
        rb = (out[..., 0] + out[..., 2]) * 0.5
        g[spill] = np.minimum(g[spill], rb[spill] * 1.02)
    return np.clip(out, 0, 255).astype(np.uint8), (alpha * 255).astype(np.uint8)


def union_bbox(alphas: list[np.ndarray], thresh: int = 24) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = 10**9, 10**9, -1, -1
    for a in alphas:
        ys, xs = np.where(a > thresh)
        if xs.size == 0:
            continue
        x0, x1 = min(x0, xs.min()), max(x1, xs.max())
        y0, y1 = min(y0, ys.min()), max(y1, ys.max())
    if x1 < 0:
        raise SystemExit("no opaque pixels found - chroma key failed")
    return int(x0), int(y0), int(x1) + 1, int(y1) + 1


def apply_matte(alphas: list[np.ndarray], matte_path: Path, box, erode: int = 4,
                dilate: int = 8) -> list[np.ndarray]:
    """Force the character silhouette from a known-good matte, keep keyed alpha only in the rim.

    The subject barely moves on these clips, so a static silhouette is enough to
    cut away any background the model invents. Anything inside the eroded core is
    always opaque; only the dilated rim still uses the chroma key.
    """
    matte = Image.open(matte_path).convert("RGBA")
    x0, y0, x1, y1 = box
    matte = matte.crop(matte.getbbox()).resize((x1 - x0, y1 - y0), Image.LANCZOS)
    full = Image.new("L", alphas[0].shape[::-1], 0)
    full.paste(matte.getchannel("A"), (x0, y0))

    core = full.point(lambda v: 255 if v > 128 else 0)
    core = core.filter(ImageFilter.MinFilter(erode * 2 + 1))
    ring = full.point(lambda v: 255 if v > 32 else 0)
    ring = ring.filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    core_np = np.array(core) > 127
    ring_np = np.array(ring) > 127

    out = []
    for a in alphas:
        merged = np.where(core_np, 255, np.where(ring_np, a, 0)).astype(np.uint8)
        soft = Image.fromarray(merged).filter(ImageFilter.GaussianBlur(0.8))
        out.append(np.array(soft))
    return out


def kill_green(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Last-pass green removal: nothing about this character is green, so any green
    pixel is spill or background that slipped through. Drop its opacity and clamp G."""
    hsv = rgb_to_hsv(rgb)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    inside = (hue > HUE_MIN) & (hue < HUE_MAX) & (sat > 0.12) & (val > 0.12)
    soft = np.zeros_like(hue)
    soft[inside] = np.clip((sat[inside] - 0.12) / 0.25, 0, 1) * (
        np.clip((val[inside] - 0.12) / 0.2, 0, 1))
    out = rgb.astype(np.float32)
    rb = (out[..., 0] + out[..., 2]) * 0.5
    out[..., 1] = np.minimum(out[..., 1], rb * 1.02)
    new_alpha = (alpha.astype(np.float32) * (1.0 - 0.98 * soft)).astype(np.uint8)
    return np.clip(out, 0, 255).astype(np.uint8), new_alpha


def stabilize(rgb, alpha, thresh: int = 24, target_h: int = 0, target_top: int = 0):
    """Lock the character's height and head position across the clip.

    Each frame is scaled so the alpha bbox height matches the clip median, then
    shifted so the bbox top and centre land on the same spot. Without this the
    model creeps forward over a long take and the swap between clips jumps.
    """
    tops, bots, lefts, rights = [], [], [], []
    for a in alpha:
        ys, xs = np.where(a > thresh)
        if ys.size == 0:
            tops.append(None); bots.append(None); lefts.append(None); rights.append(None)
        else:
            tops.append(int(ys.min())); bots.append(int(ys.max()))
            lefts.append(int(xs.min())); rights.append(int(xs.max()))

    ok = [i for i in range(len(alpha)) if tops[i] is not None and bots[i] - tops[i] > 8]
    if not ok:
        return rgb, alpha

    if not target_h:
        target_h = int(np.median([bots[i] - tops[i] for i in ok]))
    if not target_top:
        target_top = int(np.median([tops[i] for i in ok]))
    target_cx = float(np.median([(lefts[i] + rights[i]) / 2 for i in ok]))

    out_rgb, out_a = [], []
    for r, a, top, bot, lx, rx in zip(rgb, alpha, tops, bots, lefts, rights):
        if top is None:
            out_rgb.append(r); out_a.append(a); continue
        h = max(1, bot - top)
        scale = target_h / h
        if abs(scale - 1.0) < 0.005:
            out_rgb.append(r); out_a.append(a); continue
        im = Image.fromarray(np.dstack([r, a]), "RGBA")
        w2, h2 = max(1, int(round(im.width * scale))), max(1, int(round(im.height * scale)))
        im = im.resize((w2, h2), Image.LANCZOS)
        canvas = Image.new("RGBA", (r.shape[1], r.shape[0]), (0, 0, 0, 0))
        paste_y = int(round(target_top - top * scale))
        paste_x = int(round(target_cx - ((lx + rx) / 2) * scale))
        canvas.alpha_composite(im, (paste_x, paste_y))
        arr = np.array(canvas)
        out_rgb.append(arr[..., :3]); out_a.append(arr[..., 3])
    return out_rgb, out_a


def compose(rgb: np.ndarray, alpha: np.ndarray, box, out_w: int, out_h: int,
            height_frac: float = 0.70, feet_frac: float = 0.88) -> np.ndarray:
    x0, y0, x1, y1 = box
    rgba = np.dstack([rgb, alpha])
    img = Image.fromarray(rgba, "RGBA").crop(box)
    target_h = max(1, int(out_h * height_frac))
    scale = target_h / img.height
    new = (max(1, int(round(img.width * scale))), target_h)
    img = img.resize(new, Image.LANCZOS)
    canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    x = (out_w - new[0]) // 2
    y = int(out_h * feet_frac) - new[1]
    canvas.alpha_composite(img, (x, y))
    return np.array(canvas)


def encode_webm(frames: list[np.ndarray], out: Path, fps: float, crf: int = 30) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(out), mode="w", format="webm")
    rate = Fraction(fps).limit_denominator(1001) if not isinstance(fps, Fraction) else fps
    stream = container.add_stream("libvpx-vp9", rate=rate)
    h, w = frames[0].shape[:2]
    stream.width, stream.height = w, h
    stream.pix_fmt = "yuva420p"
    stream.options = {"crf": str(crf), "b:v": "0", "row-mt": "1", "cpu-used": "3"}
    for arr in frames:
        frame = av.VideoFrame.from_ndarray(arr, format="rgba")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def preview(frames: list[np.ndarray], out: Path, picks=(0, 25, 50, 75)) -> None:
    idx = [min(len(frames) - 1, int(len(frames) * p / 100)) for p in picks]
    tiles = []
    for i in idx:
        arr = frames[i]
        h, w = arr.shape[:2]
        tile = Image.new("RGB", (w, h), (240, 240, 240))
        d = ImageDraw.Draw(tile)
        for yy in range(0, h, 24):
            for xx in range(0, w, 24):
                if (xx // 24 + yy // 24) % 2:
                    d.rectangle((xx, yy, xx + 23, yy + 23), fill=(205, 205, 205))
        fg = Image.fromarray(arr, "RGBA")
        tile.paste(fg, (0, 0), fg)
        ImageDraw.Draw(tile).text((6, 6), f"frame {i}", fill=(20, 20, 20))
        tiles.append(tile)
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * len(tiles), h), (255, 255, 255))
    for k, t in enumerate(tiles):
        sheet.paste(t, (k * w, 0))
    sheet.save(out, quality=90)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("outdir")
    ap.add_argument("--name", default="idle")
    ap.add_argument("--height", type=int, default=360)
    ap.add_argument("--crf", type=int, default=30)
    ap.add_argument("--matte", help="PNG with alpha; forces the character silhouette")
    ap.add_argument("--matte-box", default="", help="x,y,w,h where the matte sits in the frame")
    ap.add_argument("--stabilize", action="store_true",
                    help="lock the character's height and head position across frames")
    ap.add_argument("--target-height", type=int, default=0,
                    help="absolute character height in the composed frame (default: clip median)")
    ap.add_argument("--target-top", type=int, default=0,
                    help="absolute y of the character's top (default: clip median)")
    ap.add_argument("--erode", type=int, default=4)
    ap.add_argument("--dilate", type=int, default=8)
    args = ap.parse_args()

    src = Path(args.src)
    outdir = Path(args.outdir)
    frames, fps = read_frames(src)
    keyed = [key_frame(f) for f in frames]
    alphas = [a for _, a in keyed]
    box = union_bbox(alphas)

    if args.matte:
        if args.matte_box:
            box = tuple(int(v) for v in args.matte_box.split(","))
        alphas = apply_matte(alphas, Path(args.matte), box,
                             erode=args.erode, dilate=args.dilate)
        keyed = [(rgb, a) for (rgb, _), a in zip(keyed, alphas)]

    # nothing on this character is green: strip the last of the spill everywhere
    keyed = [kill_green(rgb, a) for rgb, a in keyed]
    alphas = [a for _, a in keyed]

    if args.stabilize:
        rgbs, alphas = zip(*keyed)
        rgbs, alphas = stabilize(list(rgbs), list(alphas),
                                  target_h=args.target_height, target_top=args.target_top)
        keyed = list(zip(rgbs, alphas))

    out_w = int(round(args.height * 16 / 9 / 2) * 2)

    if args.stabilize and args.target_height:
        # geometry is already fixed by stabilize(); crop the whole frame and let
        # the canvas scale decide, so a prop widening the bbox cannot shrink her
        fh, fw = keyed[0][0].shape[:2]
        box = (0, 0, fw, fh)
        height_frac = args.height / fh
        feet_frac = 1.0        # keep the stabilized y so the feet stay on the floor line
    else:
        height_frac = 0.70
        feet_frac = 0.88

    composed = [compose(rgb, a, box, out_w, args.height,
                        height_frac=height_frac, feet_frac=feet_frac)
                for rgb, a in keyed]

    webm = outdir / f"{args.name}.webm"
    encode_webm(composed, webm, fps, args.crf)

    preview(composed, outdir / f"{args.name}_preview.jpg")
    seam = float(np.abs(composed[0].astype(np.int16) - composed[-1].astype(np.int16)).mean())
    meta = {
        "name": args.name,
        "source": str(src),
        "fps": fps,
        "frames": len(composed),
        "size": [out_w, args.height],
        "bbox": box,
        "loop_seam_mean_abs": round(seam, 2),
        "bytes": webm.stat().st_size,
    }
    (outdir / f"{args.name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
