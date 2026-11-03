#!/usr/bin/env python3
"""Build expression layers: procedural mouth/blush + the inpainted closed eyes.

Inpainting a 30 px mouth is unreliable on a flat illustration, so the mouth is
drawn. That keeps line colour and identity stable. Happy uses the clean
closed-eye crop; the rest keep the neutral eyes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "taozi" / "parts"
OUT = ROOT / "assets"
SOURCE = ROOT / "candidates" / "seed_1101.png"
BLINK = ROOT / "taozi" / "expressions2" / "blink2.png"
TALK = ROOT / "taozi" / "expressions2" / "mouth.png"

MOUTH_BOX = (392, 198, 428, 216)
EYE_BOXES = [(334, 136, 402, 186), (430, 134, 498, 184)]


def feathered_mask(size, boxes, blur=3.0):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for x1, y1, x2, y2 in boxes:
        draw.ellipse((x1, y1, x2, y2), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def cutout(path: Path, bg_mask: Image.Image) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    image.putalpha(ImageChops.multiply(bg_mask, image.getchannel("A")))
    return image


def main() -> None:
    base = Image.open(SOURCE).convert("RGBA")
    head = Image.open(PARTS / "head.png").convert("RGBA")
    bg_mask = base.getchannel("A")
    head_alpha = head.getchannel("A")

    # Mouth area is flat skin; sample just below the mouth.
    skin = base.getpixel((415, 190))[:3]
    mouth_mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mouth_mask).ellipse(MOUTH_BOX, fill=255)
    mouth_mask = mouth_mask.filter(ImageFilter.GaussianBlur(4))

    def blank_mouth():
        canvas = Image.open(PARTS / "head.png").convert("RGBA")
        fill = Image.new("RGBA", base.size, skin + (255,))
        return Image.composite(fill, canvas, mouth_mask)

    def draw_mouth(canvas, box, start, end, width=3, colour=(92, 52, 52, 255)):
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).arc(box, start, end, fill=colour, width=width)
        return Image.alpha_composite(canvas, overlay.filter(ImageFilter.GaussianBlur(0.4)))

    def add_blush(canvas, strength=70):
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        for x1, y1, x2, y2 in [(338, 180, 380, 204), (436, 180, 478, 204)]:
            d.ellipse((x1, y1, x2, y2), fill=(255, 140, 145, strength))
        overlay = overlay.filter(ImageFilter.GaussianBlur(7))
        return Image.alpha_composite(canvas, overlay)

    # Closed eyes from the inpainted blink, restricted to the eye boxes.
    eye_mask = feathered_mask(base.size, EYE_BOXES, 3)
    closed_eyes = cutout(BLINK, bg_mask)
    closed_eyes.putalpha(ImageChops.multiply(closed_eyes.getchannel("A"), eye_mask))

    expressions = {}

    # happy: the inpainted closed-eye head + smile
    happy = cutout(BLINK, bg_mask)
    happy = draw_mouth(happy, (398, 203, 420, 215), 15, 165, 3)
    expressions["happy"] = happy

    # sad: neutral eyes + frown
    sad = blank_mouth()
    sad = draw_mouth(sad, (398, 205, 420, 217), 195, 345, 3)
    expressions["sad"] = sad

    # angry: frown + a little blush
    angry = blank_mouth()
    angry = draw_mouth(angry, (400, 206, 418, 216), 200, 340, 3)
    angry = add_blush(angry, 45)
    expressions["angry"] = angry

    # shy: small smile + blush
    shy = blank_mouth()
    shy = draw_mouth(shy, (400, 204, 418, 214), 20, 160, 2)
    shy = add_blush(shy, 85)
    expressions["shy"] = shy

    OUT.mkdir(parents=True, exist_ok=True)
    for name, image in expressions.items():
        image.putalpha(ImageChops.multiply(image.getchannel("A"), head_alpha))
        image.save(OUT / ("expr_%s.png" % name))

    talk = cutout(TALK, bg_mask)
    talk.putalpha(
        ImageChops.multiply(
            talk.getchannel("A"), feathered_mask(base.size, [MOUTH_BOX], 3)
        )
    )
    talk.putalpha(ImageChops.multiply(talk.getchannel("A"), head_alpha))
    talk.save(OUT / "talk_mouth.png")
    print("wrote expressions + blink_eyes + talk_mouth")


if __name__ == "__main__":
    main()
