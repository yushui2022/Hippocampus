"""QC a green-screen clip before spending time on keying.

Flags the failure mode we kept hitting: the model slowly zooming into a close-up.
It measures the character bounding box on every frame and reports drift.

Usage:
    python qc_clip.py reference/h3_idle768_v3.mp4
"""

from __future__ import annotations

import argparse

import av
import numpy as np


def green_mask(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32) / 255.0
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    diff = mx - mn
    hue = np.zeros_like(mx)
    nz = diff > 1e-6
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    i = nz & (mx == r)
    hue[i] = (60 * ((g[i] - b[i]) / diff[i]) + 360) % 360
    i = nz & (mx == g)
    hue[i] = 60 * ((b[i] - r[i]) / diff[i]) + 120
    i = nz & (mx == b)
    hue[i] = 60 * ((r[i] - g[i]) / diff[i]) + 240
    sat = np.where(mx > 1e-6, diff / np.maximum(mx, 1e-6), 0.0)
    return (hue > 70) & (hue < 170) & (sat > 0.15) & (mx > 0.15)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--step", type=int, default=4)
    args = ap.parse_args()

    heights, tops, widths, bottoms = [], [], [], []
    frame_h = frame_w = 0
    with av.open(args.video) as container:
        for idx, frame in enumerate(container.decode(video=0)):
            if idx % args.step:
                continue
            rgb = frame.to_ndarray(format="rgb24")
            fg = ~green_mask(rgb)
            ys, xs = np.where(fg)
            if xs.size == 0:
                heights.append(0)
                tops.append(0)
                widths.append(0)
                continue
            frame_h, frame_w = rgb.shape[:2]
            heights.append(int(ys.max() - ys.min()))
            tops.append(int(ys.min()))
            widths.append(int(xs.max() - xs.min()))
            bottoms.append(int(frame_h - 1 - ys.max()))

    if not heights or max(heights) == 0:
        print("FAIL: nothing found outside the green background")
        return

    h0, hmax = heights[0], max(heights)
    growth = hmax / max(1, h0)
    top_min, top_max = min(tops), max(tops)
    print(f"frames sampled   {len(heights)}")
    print(f"height  first={h0}  max={hmax}  min={min(heights)}  growth={growth:.2f}x")
    print(f"top     min={top_min}  max={top_max}")
    top_margin0 = tops[0]
    bottom_margin0 = bottoms[0]
    touched = any(t <= 2 for t in tops) or any(b <= 2 for b in bottoms)
    print(f"margins first  top={top_margin0}  bottom={bottom_margin0}")
    print(f"margins min    top={min(tops)}  bottom={min(bottoms)}")

    if touched and top_margin0 > 0.02 * frame_h:
        print("FAIL: character reaches the frame edge - model zoomed into a close-up")
    elif growth > 1.6:
        print("FAIL: character grows %.2fx (zoom drift)" % growth)
    elif (top_max - top_min) > 0.10 * h0:
        print("WARN: framing drifts vertically by %d px" % (top_max - top_min))
    else:
        print("OK: framing stable")


if __name__ == "__main__":
    main()
