#!/usr/bin/env python3
"""Animation layers for the flat-shaded Taozijie illustration.

The head is one piece (face + hair + eyes + mouth) cut at the collar, with a
small feathered neck patch behind it. That keeps the composite pixel-identical
to the source while allowing head tilt, breathing, blink and talk.

Coordinates are measured from the illustration itself; the vision planner's
boxes were off by ~40 px, so they are not trusted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "candidates" / "seed_1101.png"
OUT = ROOT / "taozi" / "parts"

BG = np.array([157, 164, 171])
PIVOT = (416, 292)

# Measured from the face grid (source pixels).
BOXES = {
    "left_eye": (338, 140, 60, 42),
    "right_eye": (434, 138, 60, 42),
    "mouth": (390, 196, 38, 22),
    "neck": (360, 238, 115, 95),
}


def alpha_from_bg(rgb: np.ndarray) -> np.ndarray:
    fg = np.linalg.norm(rgb.astype(np.int16) - BG, axis=2) > 18
    fg = ndimage.binary_fill_holes(fg)
    return ndimage.binary_opening(fg, iterations=1)


def save_rgba(rgb: np.ndarray, mask: np.ndarray, path: Path) -> None:
    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.where(mask, 255, 0)
    Image.fromarray(rgba, "RGBA").save(path)


def main() -> None:
    img = Image.open(SOURCE).convert("RGB")
    rgb = np.asarray(img)
    h, w = rgb.shape[:2]
    yy = np.arange(h)[:, None] * np.ones((1, w))
    xx = np.ones((h, 1)) * np.arange(w)[None, :]

    fg = alpha_from_bg(rgb)
    a = rgb.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    black = (r < 60) & (g < 60) & (b < 60)
    skin = (r > 235) & (g > 222) & (b > 208)
    hair = (
        (r > 80)
        & (r < 200)
        & (g > 45)
        & (g < 145)
        & (b > 35)
        & (b < 130)
        & ((r - g) > 20)
    )
    iris = (r > 110) & (r < 230) & (g > 60) & (g < 180) & (b < 140)
    eye_white = (r > 244) & (g > 242) & (b > 236)

    hair = (hair | (black & (yy < 380))) & fg
    hair = ndimage.binary_closing(hair, iterations=2)
    hair = ndimage.binary_dilation(hair, iterations=1) & fg

    def box(name: str, margin: int):
        x, y, bw, bh = BOXES[name]
        return (
            max(0, x - margin),
            max(0, y - margin),
            min(w, x + bw + margin),
            min(h, y + bh + margin),
        )

    def box_mask(name: str, margin: int) -> np.ndarray:
        x1, y1, x2, y2 = box(name, margin)
        m = np.zeros((h, w), dtype=bool)
        m[y1:y2, x1:x2] = True
        return m

    eye_l = box_mask("left_eye", 2)
    eye_r = box_mask("right_eye", 2)
    eyes = (eye_l | eye_r) & fg & (black | iris | eye_white)
    eyes = ndimage.binary_dilation(eyes, iterations=1) & (eye_l | eye_r)

    mouth = box_mask("mouth", 2) & fg & (
        black | ((r > 80) & (r < 230) & (g < 130) & (b < 140))
    )

    # The head/body cut sits inside the hoodie, so a small tilt never opens
    # a seam at the collar. The neck patch behind it is feathered.
    head = (fg & (yy < 350)) | (hair & (yy < 390))
    body = fg & ~head & (yy >= 300)

    OUT.mkdir(parents=True, exist_ok=True)
    save_rgba(rgb, body, OUT / "body.png")
    save_rgba(rgb, head, OUT / "head.png")
    save_rgba(rgb, eyes, OUT / "eyes.png")
    save_rgba(rgb, mouth, OUT / "mouth.png")

    # Wide neck/shoulder patch behind the head, feathered at the edges.
    x, y, bw, bh = BOXES["neck"]
    x1, y1 = x - 90, y - 20
    x2, y2 = x + bw + 90, y + bh + 90
    neck = np.zeros((h, w), dtype=bool)
    neck[max(0, y1):min(h, y2), max(0, x1):min(w, x2)] = True
    neck = neck & fg
    alpha = neck.astype(np.uint8) * 255
    # Feather 18 px from each side.
    ramp_x = np.clip(np.minimum(np.arange(w) - x1, x2 - np.arange(w)) / 18.0, 0, 1)
    ramp_y = np.clip(np.minimum(np.arange(h) - y1, y2 - np.arange(h)) / 18.0, 0, 1)
    alpha = (alpha * ramp_x[None, :] * ramp_y[:, None]).astype(np.uint8)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = alpha
    Image.fromarray(rgba, "RGBA").save(OUT / "neck_fill.png")

    # -- blink overlay ------------------------------------------------------
    blink = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(blink)
    for name in ("left_eye", "right_eye"):
        x, y, bw, bh = BOXES[name]
        skin_colour = tuple(int(v) for v in rgb[min(h - 1, y + bh + 10), x + bw // 2])
        eye_pixels = np.zeros((h, w), dtype=bool)
        eye_pixels[y : y + bh, x : x + bw] = True
        eye_pixels &= fg & (black | iris | eye_white)
        eye_pixels = ndimage.binary_dilation(eye_pixels, iterations=2)
        ys, xs = np.where(eye_pixels)
        if len(xs) == 0:
            continue
        for yy_, xx_ in zip(ys, xs):
            blink.putpixel((int(xx_), int(yy_)), skin_colour + (255,))
        # Closed lash: the original upper lash, shifted down a little.
        lash = np.zeros((h, w), dtype=bool)
        top = y + int(bh * 0.55)
        lash[y:top, x : x + bw] = black[y:top, x : x + bw] & fg[
            y:top, x : x + bw
        ]
        lash = ndimage.binary_dilation(lash, iterations=1)
        shift = int(bh * 0.30)
        ys, xs = np.where(lash)
        for yy_, xx_ in zip(ys, xs):
            blink.putpixel((int(xx_), int(yy_ + shift)), (55, 35, 45, 255))
    blink = blink.filter(ImageFilter.GaussianBlur(0.6))
    blink.save(OUT / "blink.png")

    # -- open mouth overlay -------------------------------------------------
    x, y, bw, bh = BOXES["mouth"]
    mouth_open = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(mouth_open)
    # Erase the original smile with skin first.
    skin_colour = tuple(int(v) for v in rgb[y + bh + 6, x + bw // 2])
    d.ellipse((x - 3, y - 3, x + bw + 3, y + bh + 3), fill=skin_colour + (255,))
    cx, cy = x + bw / 2, y + bh / 2 + 2
    rx, ry = bw * 0.46, bh * 0.55
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(92, 48, 58, 255))
    d.ellipse(
        (cx - rx * 0.55, cy + ry * 0.15, cx + rx * 0.55, cy + ry * 0.9),
        fill=(190, 105, 115, 255),
    )
    mouth_open = mouth_open.filter(ImageFilter.GaussianBlur(0.5))
    mouth_open.save(OUT / "mouth_open.png")

    # -- composite check ----------------------------------------------------
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for name in ("body", "neck_fill", "head", "eyes", "mouth"):
        canvas.alpha_composite(Image.open(OUT / (name + ".png")))
    canvas.save(OUT / "composite.png")
    canvas.convert("RGB").save(OUT / "composite.jpg", quality=92)

    meta = {
        "source": str(SOURCE),
        "size": [w, h],
        "pivot": list(PIVOT),
        "boxes": BOXES,
    }
    (OUT / "parts.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
