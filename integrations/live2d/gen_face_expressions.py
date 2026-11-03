#!/usr/bin/env python3
"""Generate facial expression variants with local ComfyUI inpainting."""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

COMFY = "http://127.0.0.1:8188"
COMFY_INPUT = Path("/Users/orlando/Documents/默认项目/ComfyUI/input")
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "candidates" / "seed_1101.png"
OUT = ROOT / "taozi" / "expressions3"
OUT.mkdir(parents=True, exist_ok=True)

EYES = [(338, 140, 60, 42), (434, 138, 60, 42)]
MOUTH = (390, 196, 38, 22)
CHEEKS = [(336, 176, 44, 26), (452, 176, 44, 26)]

PROMPTS = {
    "happy": (
        "happy, smiling, closed smiling eyes, open smile, cheerful, "
        "same character, same art style, anime lineart",
        "sad, angry, neutral, open eyes, text, watermark",
    ),
    "sad": (
        "sad, teary eyes, downturned mouth, drooping brows, "
        "same character, same art style, anime lineart",
        "happy, smiling, angry, text, watermark",
    ),
    "angry": (
        "angry, furrowed brows, pouting mouth, annoyed, "
        "same character, same art style, anime lineart",
        "happy, sad, smiling, text, watermark",
    ),
    "shy": (
        "shy, blushing cheeks, small smile, looking away, embarrassed, "
        "same character, same art style, anime lineart",
        "angry, sad, open mouth, text, watermark",
    ),
}


def make_mask(kind: str, path: Path) -> None:
    src = Image.open(SOURCE)
    w, h = src.size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    for x, y, bw, bh in EYES:
        d.ellipse((x - 4, y - 6, x + bw + 4, y + bh + 6), fill=255)
    x, y, bw, bh = MOUTH
    d.ellipse((x - 6, y - 5, x + bw + 6, y + bh + 6), fill=255)
    if kind == "shy":
        for x, y, bw, bh in CHEEKS:
            d.ellipse((x, y, x + bw, y + bh), fill=255)
    m.save(path)


def workflow(mask_name: str, positive: str, negative: str, seed: int, denoise: float) -> dict:
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "NoobAI-XL-v1.1.safetensors"},
        },
        "2": {
            "class_type": "CLIPSetLastLayer",
            "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2},
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": positive}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "5": {"class_type": "LoadImage", "inputs": {"image": "taozi_base.png"}},
        "6": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
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
                "steps": 28,
                "cfg": 6.5,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": denoise,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "taozi_expr"}},
    }


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        COMFY + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def wait(prompt_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(COMFY + "/history/" + prompt_id, timeout=60) as resp:
            item = json.loads(resp.read().decode()).get(prompt_id)
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
            with urllib.request.urlopen(COMFY + "/view?" + q, timeout=120) as resp:
                target.write_bytes(resp.read())
            return
    raise RuntimeError("no image")


def main() -> None:
    shutil.copy2(SOURCE, COMFY_INPUT / "taozi_base.png")
    src = Image.open(SOURCE).convert("RGBA")
    for kind, (pos, neg) in PROMPTS.items():
        mask_path = OUT / ("mask_%s.png" % kind)
        make_mask(kind, mask_path)
        shutil.copy2(mask_path, COMFY_INPUT / mask_path.name)
        soft = mask_path.read_bytes()
        for seed in (7101, 7202):
            pid = post(
                "/prompt",
                {
                    "prompt": workflow(mask_path.name, pos, neg, seed, 0.62),
                    "client_id": "taozi-expr3",
                },
            )["prompt_id"]
            item = wait(pid)
            raw = OUT / ("%s_%d_raw.png" % (kind, seed))
            save(item, raw)
            generated = Image.open(raw).convert("RGBA")
            mask = Image.open(mask_path).convert("L").filter(ImageFilter.GaussianBlur(2.0))
            out = Image.composite(generated, src, mask)
            out.convert("RGB").save(OUT / ("%s_%d.png" % (kind, seed)))
            print("saved", kind, seed, flush=True)


if __name__ == "__main__":
    main()
