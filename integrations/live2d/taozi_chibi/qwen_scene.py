"""Text-to-image with local Qwen-Image 2.1, sized for a stream background.

    python qwen_scene.py --prompt prompts/bedroom.txt --width 1344 --height 768 --out reference/bedroom.png

Unlike qwen_upscale.py this needs no reference image: the conditioning comes from
the prompt only and the latent is a plain empty latent at the requested size.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

C = "http://127.0.0.1:8188"


def build(prompt: str, negative: str, width: int, height: int, steps: int, cfg: float,
          seed: int, unet: str, prefix: str):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.1, "sampling": "flow"}},
        "3": {"class_type": "CFGNorm",
              "inputs": {"model": ["2", 0], "strength": 1.0, "pre_cfg": False}},
        "4": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image"}},
        "5": {"class_type": "VAELoader",
              "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "6": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["4", 0], "prompt": prompt, "negative_prompt": negative,
                         "vae": ["5", 0], "resolution": 0}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["3", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "euler", "scheduler": "simple",
                         "positive": ["6", 0], "negative": ["6", 1],
                         "latent_image": ["7", 0], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["5", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="path to a prompt text file")
    ap.add_argument("--negative", default="人物, 人, 角色, 文字, 水印, 签名, 低质量, 模糊, 畸变")
    ap.add_argument("--width", type=int, default=1344)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--unet", default="qwen_image_2.1_turbo_int8_convrot.safetensors")
    ap.add_argument("--prefix", default="scene")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    prompt = Path(args.prompt).read_text().strip()
    body = json.dumps({"prompt": build(prompt, args.negative, args.width, args.height,
                                       args.steps, args.cfg, args.seed, args.unet, args.prefix),
                       "client_id": str(uuid.uuid4())}).encode()
    req = urllib.request.Request(C + "/prompt", data=body,
                                 headers={"Content-Type": "application/json"})
    pid = json.loads(urllib.request.urlopen(req, timeout=120).read())["prompt_id"]
    print("prompt_id", pid, flush=True)

    t0 = time.time()
    while True:
        item = json.loads(urllib.request.urlopen(C + "/history/" + pid, timeout=60).read()).get(pid)
        if item:
            break
        time.sleep(6)

    print("done in %.0fs" % (time.time() - t0), flush=True)
    if item.get("status", {}).get("status_str") != "success":
        print(json.dumps(item.get("status", {}))[:1200], flush=True)
        return
    for out in item.get("outputs", {}).values():
        for img in out.get("images", []):
            qs = urllib.parse.urlencode({"filename": img["filename"],
                                         "subfolder": img.get("subfolder", ""),
                                         "type": img.get("type", "output")})
            data = urllib.request.urlopen(C + "/view?" + qs, timeout=300).read()
            Path(args.out).write_bytes(data)
            print("saved", args.out, len(data), flush=True)


if __name__ == "__main__":
    main()
