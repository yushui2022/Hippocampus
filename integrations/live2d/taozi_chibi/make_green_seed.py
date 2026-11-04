"""Composite a cutout onto a chroma-green canvas sized for MiniMax-H3.

The H3 node stretches `first_frame` to the canvas, so the canvas must already be
the target aspect (16:9) or the character gets squashed.

Usage:
    python make_green_seed.py --src ../reference/q_front_cutout.png \
        --out q_loop_768.png --width 1344 --height 768 --height-frac 0.70 --feet-frac 0.88
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--height-frac", type=float, default=0.70,
                    help="character height as a fraction of the canvas")
    ap.add_argument("--feet-frac", type=float, default=0.88,
                    help="where the feet sit vertically")
    ap.add_argument("--pad", type=int, default=0, help="extra margin around the cutout bbox")
    args = ap.parse_args()

    src = Image.open(args.src).convert("RGBA")
    bbox = src.getbbox()
    if args.pad:
        bbox = (max(0, bbox[0] - args.pad), max(0, bbox[1] - args.pad),
                min(src.width, bbox[2] + args.pad), min(src.height, bbox[3] + args.pad))
    ch = src.crop(bbox)

    target_h = max(1, int(args.height_frac * args.height))
    ch = ch.resize((max(1, int(round(ch.width * target_h / ch.height))), target_h),
                   Image.LANCZOS)

    canvas = Image.new("RGBA", (args.width, args.height), (0, 255, 0, 255))
    x = (args.width - ch.width) // 2
    y = int(args.feet_frac * args.height) - ch.height
    canvas.alpha_composite(ch, (x, y))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out)

    box = [x, y, x + ch.width, y + ch.height]
    meta = {"canvas": [args.width, args.height], "character": [ch.width, ch.height],
            "matte_box": box, "height_frac": args.height_frac, "feet_frac": args.feet_frac}
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
