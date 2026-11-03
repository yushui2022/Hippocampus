#!/usr/bin/env python3
"""Layered parts for the whale-maid character (seed_8404)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "candidates_whale" / "seed_8404.png"
OUT = ROOT / "taozi_whale" / "parts"

CUT = 330
PIVOT = (416, 300)
BOXES = {
    "left_eye": (294, 218, 62, 42),
    "right_eye": (462, 218, 62, 42),
    "mouth": (394, 258, 44, 24),
    "neck": (340, 270, 160, 130),
}


def main() -> None:
    rgb = np.asarray(Image.open(SOURCE).convert("RGB"))
    h, w = rgb.shape[:2]
    yy = np.arange(h)[:, None] * np.ones((1, w))
    a = rgb.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]

    bg = np.array([227, 227, 226])
    fg = np.linalg.norm(a - bg, axis=2) > 12
    fg = ndimage.binary_fill_holes(fg)
    fg = ndimage.binary_opening(fg, iterations=1)

    head = fg & (yy < CUT)
    body = fg & ~head & (yy >= CUT - 30)

    OUT.mkdir(parents=True, exist_ok=True)

    def save(mask, name):
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3] = rgb
        rgba[..., 3] = np.where(mask, 255, 0)
        Image.fromarray(rgba, "RGBA").save(OUT / name)

    save(body, "body.png")
    save(head, "head.png")

    # feathered neck patch behind the head
    x, y, bw, bh = BOXES["neck"]
    x1, y1, x2, y2 = x - 25, y - 15, x + bw + 25, y + bh + 30
    neck = np.zeros((h, w), dtype=bool)
    neck[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)] = True
    neck &= fg
    alpha = neck.astype(np.uint8) * 255
    rx = np.clip(np.minimum(np.arange(w) - x1, x2 - np.arange(w)) / 18.0, 0, 1)
    ry = np.clip(np.minimum(np.arange(h) - y1, y2 - np.arange(h)) / 18.0, 0, 1)
    alpha = (alpha * rx[None, :] * ry[:, None]).astype(np.uint8)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = alpha
    Image.fromarray(rgba, "RGBA").save(OUT / "neck_fill.png")

    # eye / mouth masks for overlays
    def region(name, margin):
        x, y, bw, bh = BOXES[name]
        m = np.zeros((h, w), dtype=bool)
        m[max(0, y - margin) : y + bh + margin, max(0, x - margin) : x + bw + margin] = True
        return m

    black = (r < 55) & (g < 55) & (b < 70)
    iris = (b > 120) & (b > g) & (g > 70) & (r < 140)
    white = (r > 242) & (g > 242) & (b > 238)
    eye = (region("left_eye", 2) | region("right_eye", 2)) & fg & (black | iris | white)
    eye = ndimage.binary_dilation(eye, iterations=1)
    save(eye, "eyes.png")

    mouth = (
        region("mouth", 2)
        & fg
        & ((r > 80) & (r < 220) & (g < 130) & (b < 150))
    )
    save(mouth, "mouth.png")

    # composite check
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for name in ("body", "neck_fill", "head"):
        canvas.alpha_composite(Image.open(OUT / (name + ".png")))
    canvas.convert("RGB").save(OUT / "composite.jpg", quality=92)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
