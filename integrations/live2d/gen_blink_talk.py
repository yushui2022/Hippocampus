#!/usr/bin/env python3
"""Blink and open-mouth overlays for both forms (ComfyUI inpainting)."""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

COMFY = "http://127.0.0.1:8188"
COMFY_INPUT = Path("/Users/orlando/Documents/默认项目/ComfyUI/input")
ROOT = Path(__file__).resolve().parent

FORMS = {
    "big": {
        "source": ROOT / "candidates_whale" / "seed_8404.png",
        "head": ROOT / "taozi_whale" / "parts" / "head.png",
        "eyes": [(294, 218, 62, 42), (462, 218, 62, 42)],
        "mouth": (394, 258, 44, 24),
        "out": ROOT / "taozi_whale" / "overlays",
    },
    "small": {
        "source": ROOT / "candidates_chibi" / "seed_9101.png",
        "head": ROOT / "taozi_chibi" / "parts" / "head.png",
        "eyes": [(334, 714, 78, 82), (488, 714, 78, 82)],
        "mouth": (412, 822, 54, 36),
        "out": ROOT / "taozi_chibi" / "overlays",
    },
}


def workflow(base: str, mask: str, pos: str, neg: str, seed: int, denoise: float) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "NoobAI-XL-v1.1.safetensors"}},
        "2": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": pos}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": neg}},
        "5": {"class_type": "LoadImage", "inputs": {"image": base}},
        "6": {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}},
        "7": {
            "class_type": "VAEEncodeForInpaint",
            "inputs": {"pixels": ["5", 0], "vae": ["1", 2], "mask": ["6", 0], "grow_mask_by": 6},
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
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "bt"}},
    }


def post(payload: dict) -> dict:
    req = urllib.request.Request(
        COMFY + "/prompt", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def wait(pid: str) -> dict:
    while True:
        with urllib.request.urlopen(COMFY + "/history/" + pid, timeout=60) as resp:
            item = json.loads(resp.read().decode()).get(pid)
        if item:
            return item
        time.sleep(2)


def save(item: dict, target: Path) -> None:
    for node in item.get("outputs", {}).values():
        for image in node.get("images", []):
            q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": "", "type": "output"})
            with urllib.request.urlopen(COMFY + "/view?" + q, timeout=120) as r:
                target.write_bytes(r.read())
            return


def make_mask(size, boxes, path, blur=3):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for x, y, w, h in boxes:
        draw.ellipse((x - 6, y - 6, x + w + 6, y + h + 6), fill=255)
    mask.filter(ImageFilter.GaussianBlur(blur)).save(path)
    return path


def main() -> None:
    for name, cfg in FORMS.items():
        source = Image.open(cfg["source"]).convert("RGBA")
        head = Image.open(cfg["head"]).convert("RGBA")
        size = source.size
        out = cfg["out"]
        out.mkdir(parents=True, exist_ok=True)
        bg = source.getchannel("A")
        head_alpha = head.getchannel("A")

        eyes_mask = make_mask(size, cfg["eyes"], out / "mask_eyes.png")
        mouth_mask = make_mask(size, [cfg["mouth"]], out / "mask_mouth.png")
        shutil.copy2(cfg["source"], COMFY_INPUT / (name + "_base.png"))
        shutil.copy2(eyes_mask, COMFY_INPUT / eyes_mask.name.replace("mask", name + "_mask"))
        shutil.copy2(mouth_mask, COMFY_INPUT / mouth_mask.name.replace("mask", name + "_mask"))

        jobs = [
            (
                "blink",
                name + "_mask_eyes.png",
                "both eyes closed, closed eyelids, eyelashes, calm, same character, same art style, lineart",
                "open eyes, iris, pupils, extra eyes, text, watermark",
                9500,
                0.62,
            ),
            (
                "talk",
                name + "_mask_mouth.png",
                "open mouth, talking, small open mouth, same character, same art style, lineart",
                "closed mouth, smile, extra mouth, text, watermark",
                9501,
                0.58,
            ),
        ]
        for kind, mask_name, pos, neg, seed, denoise in jobs:
            pid = post(
                {
                    "prompt": workflow(name + "_base.png", mask_name, pos, neg, seed, denoise),
                    "client_id": "bt-" + name,
                }
            )["prompt_id"]
            item = wait(pid)
            raw = out / (kind + "_raw.png")
            save(item, raw)
            generated = Image.open(raw).convert("RGBA")
            mk = Image.open(out / ("mask_eyes.png" if kind == "blink" else "mask_mouth.png")).convert("L")
            mk = mk.filter(ImageFilter.GaussianBlur(2))
            merged = Image.composite(generated, source, mk)
            if kind == "blink":
                merged.putalpha(ImageChops.multiply(bg, merged.getchannel("A")))
                merged.putalpha(ImageChops.multiply(merged.getchannel("A"), head_alpha))
                merged.save(out / "blink_head.png")
            else:
                merged.putalpha(ImageChops.multiply(bg, merged.getchannel("A")))
                merged.putalpha(ImageChops.multiply(merged.getchannel("A"), mk))
                merged.putalpha(ImageChops.multiply(merged.getchannel("A"), head_alpha))
                merged.save(out / "talk_mouth.png")
            print("saved", name, kind, flush=True)


if __name__ == "__main__":
    main()
