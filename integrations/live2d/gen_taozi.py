#!/usr/bin/env python3
"""Generate Taozijie candidates through the local ComfyUI (NoobAI-XL).

No external API. Outputs land in ./candidates/.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
OUT = Path(__file__).resolve().parent / "candidates"
OUT.mkdir(parents=True, exist_ok=True)

POSITIVE = (
    "masterpiece, best quality, 1girl, solo, full body, standing, front view, "
    "looking at viewer, brown bob hair, brown eyes, peach hoodie, black shorts, "
    "black thighhighs, gentle smile, arms at sides, simple background, "
    "grey background, soft lighting, clean lineart"
)
NEGATIVE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, extra fingers, "
    "missing fingers, extra limbs, cropped, out of frame, multiple views, text, "
    "watermark, signature, nsfw, complex background, scenery"
)


def workflow(seed: int) -> dict:
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
            "inputs": {"clip": ["2", 0], "text": POSITIVE},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": NEGATIVE},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 832, "height": 1216, "batch_size": 1},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": 26,
                "cfg": 5.5,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["7", 0],
                "filename_prefix": "taozi_candidate",
            },
        },
    }


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        COMFY + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(COMFY + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait(prompt_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = get("/history/" + prompt_id)
        item = history.get(prompt_id)
        if item:
            return item
        time.sleep(2)
    raise TimeoutError(prompt_id)


def save_image(item: dict, target: Path) -> None:
    for node in item.get("outputs", {}).values():
        for image in node.get("images", []):
            query = urllib.parse.urlencode(
                {
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
            )
            with urllib.request.urlopen(
                COMFY + "/view?" + query, timeout=120
            ) as resp:
                target.write_bytes(resp.read())
            return
    raise RuntimeError("no image in result")


def main() -> None:
    seeds = [1101, 2202, 3303, 4404]
    queued = []
    for seed in seeds:
        result = post(
            "/prompt",
            {"prompt": workflow(seed), "client_id": "taozi-gen"},
        )
        queued.append((seed, result["prompt_id"]))
        print("queued", seed, result["prompt_id"], flush=True)

    for seed, prompt_id in queued:
        started = time.time()
        item = wait(prompt_id)
        target = OUT / ("seed_%d.png" % seed)
        save_image(item, target)
        print(
            "saved", target, "in %.1fs" % (time.time() - started), flush=True
        )


if __name__ == "__main__":
    main()
