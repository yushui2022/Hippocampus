"""Upscale the chibi art with local Qwen-Image 2.1 before handing it to the video model.

TextEncodeQwenImage21 treats the reference image as an edit target: it resizes the
reference to ~resolution^2 pixels and returns a latent at that size, so the sampler
re-renders the same picture at higher resolution instead of inventing a new one.

Usage:
    python qwen_upscale.py --resolution 2048 --steps 24 --cfg 2.5
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
import uuid
import shutil
from pathlib import Path

C = "http://127.0.0.1:8188"
COMFY_INPUT = Path("/Users/orlando/Documents/默认项目/ComfyUI/input")

PROMPT = (
    "把这张 Q 版角色插画重绘为高分辨率版本。"
    "严格保持原图的角色设计：同样的蓝色长发、同样的发饰和蝴蝶结、同样的女仆装与白色围裙、"
    "同样的鲸鱼尾巴、同样的五官比例和表情、同样的深蓝配色。"
    "保持原来的动漫 Q 版画风、粗轮廓线和赛璐璐上色。"
    "只是把线条和色块画得更清晰、更干净、更锐利，补足放大后缺失的细节。"
    "背景必须是纯绿色 #00FF00 的单一色块，像绿幕一样，不能有黑色或其他颜色。"
    "角色完整居中，不要裁切。"
)
NEGATIVE = (
    "改变设计, 换脸, 换发型, 换衣服, 不同配色, 写实风格, 3D 渲染, 背景, 场景, "
    "文字, 水印, 签名, 模糊, 低分辨率, 多余的肢体, 畸形"
)


def build(image: str, resolution: int, steps: int, cfg: float, seed: int,
          unet: str, sampler: str, scheduler: str, shift: float, prefix: str,
          prompt_text: str = PROMPT):
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": shift, "sampling": "flow"}},
        "3": {"class_type": "CFGNorm",
              "inputs": {"model": ["2", 0], "strength": 1.0, "pre_cfg": False}},
        "4": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image"}},
        "5": {"class_type": "VAELoader",
              "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "6": {"class_type": "LoadImage", "inputs": {"image": image}},
        "7": {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["4", 0], "prompt": prompt_text, "negative_prompt": NEGATIVE,
                         "vae": ["5", 0], "resolution": resolution,
                         "images.image_1": ["6", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["3", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": sampler, "scheduler": scheduler,
                         "positive": ["7", 0], "negative": ["7", 1],
                         "latent_image": ["7", 2], "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["5", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="q_front_cutout.png")
    ap.add_argument("--resolution", type=int, default=2048)
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--cfg", type=float, default=2.5)
    ap.add_argument("--seed", type=int, default=31415)
    ap.add_argument("--unet", default="qwen_image_2.1_int8_convrot.safetensors")
    ap.add_argument("--sampler", default="euler")
    ap.add_argument("--scheduler", default="simple")
    ap.add_argument("--shift", type=float, default=3.1)
    ap.add_argument("--prefix", default="qwen_up")
    ap.add_argument("--out", default="")
    ap.add_argument("--prompt-file", default="", help="override the built-in prompt")
    args = ap.parse_args()

    # LoadImage resolves names inside ComfyUI/input; a missing file silently drops
    # the reference and turns the edit into plain text-to-image
    src = Path(args.image)
    if src.exists():
        COMFY_INPUT.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, COMFY_INPUT / src.name)
        image_name = src.name
    else:
        image_name = args.image

    prompt_text = Path(args.prompt_file).read_text().strip() if args.prompt_file else PROMPT
    body = json.dumps({"prompt": build(image_name, args.resolution, args.steps, args.cfg,
                                       args.seed, args.unet, args.sampler, args.scheduler,
                                       args.shift, args.prefix, prompt_text),
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
        time.sleep(8)

    print("done in %.0fs" % (time.time() - t0), flush=True)
    status = item.get("status", {})
    if status.get("status_str") != "success":
        print(json.dumps(status)[:1200], flush=True)
        return
    for out in item.get("outputs", {}).values():
        for img in out.get("images", []):
            qs = urllib.parse.urlencode({"filename": img["filename"],
                                         "subfolder": img.get("subfolder", ""),
                                         "type": img.get("type", "output")})
            data = urllib.request.urlopen(C + "/view?" + qs, timeout=300).read()
            dst = Path(args.out) if args.out else Path("/Users/orlando/Documents/蓝色大肥鱼/live2d/reference/qwen_up.png")
            dst.write_bytes(data)
            print("saved", dst, len(data), flush=True)


if __name__ == "__main__":
    main()
