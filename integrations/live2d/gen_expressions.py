#!/usr/bin/env python3
"""Generate blink / open-mouth variants with local ComfyUI inpainting.

Only the masked area is regenerated; the rest of the illustration is copied
back from the source, so colours and lines stay identical.
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

COMFY = "http://127.0.0.1:8188"
COMFY_INPUT = Path("/Users/orlando/Documents/默认项目/ComfyUI/input")
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "candidates" / "seed_1101.png"
OUT = ROOT / "taozi" / "expressions2"
OUT.mkdir(parents=True, exist_ok=True)

BOXES = {
    "left_eye": (338, 140, 60, 42),
    "right_eye": (434, 138, 60, 42),
    "mouth": (390, 196, 38, 22),
}


def make_mask(name: str, path: Path, kind: str) -> None:
    src = Image.open(SOURCE)
    w, h = src.size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    if kind == "eyes":
        for key in ("left_eye", "right_eye"):
            x, y, bw, bh = BOXES[key]
            d.ellipse((x - 6, y - 4, x + bw + 6, y + bh + 4), fill=255)
    else:
        x, y, bw, bh = BOXES["mouth"]
        d.ellipse((x - 5, y - 4, x + bw + 5, y + bh + 5), fill=255)
    m.save(path)


def workflow(base: str, mask: str, positive: str, negative: str, seed: int, denoise: float) -> dict:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "NoobAI-XL-v1.1.safetensors"},
        },
        "2": {
            "class_type": "CLIPSetLastLayer",
            "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": positive},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": negative},
        },
        "5": {"class_type": "LoadImage", "inputs": {"image": base}},
        "6": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "7": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {
                "pixels": ["5", 0],
                "vae": ["1", 2],
                "mask": ["6", 0],
                "grow_mask_by": 8,
            },
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["7", 0],
                "seed": seed,
                "steps": 26,
                "cfg": 6.0,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": denoise,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {
            "class_type": "SaveImage",
            "inputs": {"images": ["9", 0], "filename_prefix": "taozi_expr"},
        },
    }


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        COMFY + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def get(path: str) -> dict:
    with urllib.request.urlopen(COMFY + path, timeout=60) as resp:
        return json.loads(resp.read().decode())


def wait(prompt_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        item = get("/history/" + prompt_id).get(prompt_id)
        if item:
            return item
        time.sleep(2)
    raise TimeoutError(prompt_id)


def save(item: dict, target: Path) -> None:
    for node in item.get("outputs", {}).values():
        for image in node.get("images", []):
            q = urllib.parse.urlencode(
                {"filename": image["filename"], "subfolder": "", "type": "output"}
            )
            with urllib.request.urlopen(COMFY + "/view?" + q, timeout=120) as r:
                target.write_bytes(r.read())
            return
    raise RuntimeError("no image")


def blend(original: Path, generated: Path, mask: Path, target: Path) -> None:
    src = Image.open(original).convert("RGB")
    gen = Image.open(generated).convert("RGB")
    m = Image.open(mask).convert("L")
    out = Image.composite(gen, src, m)
    out.save(target)


def main() -> None:
    shutil.copy2(SOURCE, COMFY_INPUT / "taozi_base.png")
    eyes_mask = OUT / "mask_eyes.png"
    mouth_mask = OUT / "mask_mouth.png"
    make_mask("eyes", eyes_mask, "eyes")
    make_mask("mouth", mouth_mask, "mouth")
    shutil.copy2(eyes_mask, COMFY_INPUT / eyes_mask.name)
    shutil.copy2(mouth_mask, COMFY_INPUT / mouth_mask.name)

    jobs = [
        (
            "blink",
            eyes_mask.name,
            "closed eyes, closed eyelids, eyelashes, calm, same character, same art style, lineart",
            "open eyes, pupils, iris, extra eyes, text, watermark",
            7701,
            0.60,
        ),
        (
            "mouth",
            mouth_mask.name,
            "open mouth, talking, small open mouth, teeth, tongue, same character, same art style",
            "closed mouth, smile, extra mouth, text, watermark",
            7702,
            0.55,
        ),
    ]
    queued = []
    for name, mask, pos, neg, seed, denoise in jobs:
        result = post(
            "/prompt",
            {
                "prompt": workflow("taozi_base.png", mask, pos, neg, seed, denoise),
                "client_id": "taozi-expr",
            },
        )
        queued.append((name, mask, result["prompt_id"]))
        print("queued", name, result["prompt_id"], flush=True)

    for name, mask, prompt_id in queued:
        item = wait(prompt_id)
        raw = OUT / (name + "_raw.png")
        save(item, raw)
        blend(SOURCE, raw, OUT / ("mask_%s.png" % ("eyes" if name == "blink" else "mouth")), OUT / (name + ".png"))
        print("saved", OUT / (name + ".png"), flush=True)


if __name__ == "__main__":
    main()
