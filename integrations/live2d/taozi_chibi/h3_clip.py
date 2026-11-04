"""Generate a green-screen clip with MiniMax H3 (FL2V: first frame == last frame -> seamless loop).

Usage:
    python h3_clip.py --name idle --prompt prompts/idle.txt --seed 1 [--cfg 1.5] [--steps 4]
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
LORA = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"


def build(prompt: str, negative: str, image: str, width: int, height: int,
          length: int, steps: int, cfg: float, seed: int, prefix: str,
          unet: str, lora: str, lora_strength: float, scheduler: str):
    g = {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": unet}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": lora,
                         "strength_model": lora_strength}},
        "3": {"class_type": "CLIPLoaderGGUF",
              "inputs": {"clip_name": "qwen3vl_32b_minimax_h3-Q4_K_M.gguf", "type": "minimax"}},
        "4": {"class_type": "VAELoader",
              "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": image}},
        "6": {"class_type": "MiniMaxH3ImageToVideo",
              "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": prompt,
                         "width": width, "height": height, "length": length,
                         "first_frame": ["5", 0], "last_frame": ["5", 0]}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "BasicScheduler",
              "inputs": {"model": ["2", 0], "scheduler": scheduler,
                         "steps": steps, "denoise": 1.0}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 24.0}},
        "13": {"class_type": "SaveVideo",
               "inputs": {"video": ["11", 0], "filename_prefix": prefix,
                          "format": "mp4", "codec": "h264"}},
    }
    if cfg > 1.0:
        g["14"] = {"class_type": "CLIPTextEncode",
                   "inputs": {"clip": ["3", 0], "text": negative}}
        g["15"] = {"class_type": "CFGGuider",
                   "inputs": {"model": ["2", 0], "positive": ["6", 0], "negative": ["14", 0],
                              "cfg": cfg}}
        g["12"] = {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["9", 0], "guider": ["15", 0], "sampler": ["7", 0],
                              "sigmas": ["8", 0], "latent_image": ["6", 1]}}
    else:
        g["15"] = {"class_type": "BasicGuider",
                   "inputs": {"model": ["2", 0], "conditioning": ["6", 0]}}
        g["12"] = {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["9", 0], "guider": ["15", 0], "sampler": ["7", 0],
                              "sigmas": ["8", 0], "latent_image": ["6", 1]}}
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--prompt", required=True, help="path to prompt text file")
    ap.add_argument("--negative", default="")
    ap.add_argument("--image", default="q_loop_test.png")
    ap.add_argument("--width", type=int, default=864)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--length", type=int, default=124)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--unet", default="MiniMax-H3-FL2VA-Pruned-Q5_K_M.gguf")
    ap.add_argument("--lora", default=LORA)
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--scheduler", default="simple")
    args = ap.parse_args()

    prompt = Path(args.prompt).read_text().strip()
    negative = args.negative.strip()
    prefix = "video/h3_" + args.name
    body = json.dumps({"prompt": build(prompt, negative, args.image, args.width, args.height,
                                      args.length, args.steps, args.cfg, args.seed, prefix,
                                      args.unet, args.lora, args.lora_strength, args.scheduler),
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
        time.sleep(10)

    print("done in %.0fs" % (time.time() - t0), flush=True)
    status = item.get("status", {})
    if status.get("status_str") != "success":
        print(json.dumps(status)[:1500], flush=True)
        return
    for out in item.get("outputs", {}).values():
        for v in (out.get("videos") or out.get("images") or []):
            qs = urllib.parse.urlencode({"filename": v["filename"],
                                         "subfolder": v.get("subfolder", ""),
                                         "type": v.get("type", "output")})
            data = urllib.request.urlopen(C + "/view?" + qs, timeout=600).read()
            dst = Path("/Users/orlando/Documents/蓝色大肥鱼/live2d/reference") / f"h3_{args.name}.mp4"
            dst.write_bytes(data)
            print("saved", dst, len(data), flush=True)


if __name__ == "__main__":
    main()
