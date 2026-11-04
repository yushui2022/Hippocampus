"""Compare a generated frame against reference art at the same character height.

Usage:
    python cmp_frame.py --video reference/x.mp4 --frame 0 \
        --art reference/q_front_cutout.png --box 657,76,1263,1033 --out cmp.jpg
"""

from __future__ import annotations

import argparse

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def read_frame(path, index):
    with av.open(path) as container:
        for i, frame in enumerate(container.decode(video=0)):
            if i == index:
                return Image.fromarray(frame.to_ndarray(format="rgb24"))
    raise SystemExit("cannot read frame %d from %s" % (index, path))


def norm(im: Image.Image, h: int) -> Image.Image:
    return im.convert("RGB").resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)


def sharp(im: Image.Image) -> float:
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = (-4.0 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--art", required=True)
    ap.add_argument("--box", default="", help="x0,y0,x1,y1 of the character in the video")
    ap.add_argument("--extra", default="", help="second video to compare")
    ap.add_argument("--out", default="reference/cmp_frame.jpg")
    ap.add_argument("--height", type=int, default=597)
    ap.add_argument("--zoom", type=float, default=2.0)
    args = ap.parse_args()

    art = Image.open(args.art).convert("RGBA")
    art = art.crop(art.getbbox())
    tiles = [("原画", norm(art, args.height))]

    frame = read_frame(args.video, args.frame)
    if args.box:
        x0, y0, x1, y1 = (int(v) for v in args.box.split(","))
        frame = frame.crop((x0, y0, x1, y1))
    tiles.append(("生成", norm(frame, args.height)))

    if args.extra:
        ex = read_frame(args.extra, args.frame)
        if args.box:
            x0, y0, x1, y1 = (int(v) for v in args.box.split(","))
            ex = ex.crop((x0, y0, x1, y1))
        tiles.append(("对照", norm(ex, args.height)))

    box = lambda im: (int(im.width * 0.16), 0, int(im.width * 0.84), int(im.height * 0.45))
    crops = []
    for label, im in tiles:
        c = im.crop(box(im))
        crops.append((label, c, sharp(c)))

    w = crops[0][1].width
    sheet = Image.new("RGB", (w * len(crops) + 16 * (len(crops) - 1), crops[0][1].height), (18, 16, 22))
    f = ImageFont.truetype(FONT, 20)
    for i, (label, c, s) in enumerate(crops):
        sheet.paste(c, (i * (w + 16), 0))
        ImageDraw.Draw(sheet).text((i * (w + 16) + 6, 6), f"{label} {s:.0f}", font=f, fill=(255, 255, 0))
        print(f"{label}: sharpness {s:.0f}")
    sheet = sheet.resize((int(sheet.width * args.zoom), int(sheet.height * args.zoom)), Image.LANCZOS)
    sheet.save(args.out, quality=95)
    print("saved", args.out)


if __name__ == "__main__":
    main()
