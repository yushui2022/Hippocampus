"""Render a one-page explainer of how the avatar actually moves."""

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, "taozi_chibi")
from h3_to_webm import read_frames, key_frame  # noqa: E402

ROOT = Path("/Users/orlando/Documents/蓝色大肥鱼/live2d")
OUT = ROOT / "reference" / "tech-explainer.png"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"

BG = (22, 20, 26)
PANEL = (32, 29, 38)
LINE = (78, 72, 92)
TEXT = (236, 233, 242)
DIM = (168, 162, 182)
ACCENT = (126, 199, 255)
WARM = (255, 176, 120)


def f(size, bold=False):
    return ImageFont.truetype(FONT, size, index=1 if bold else 0)


def checker(w, h, cell=12):
    im = Image.new("RGB", (w, h), (238, 238, 238))
    d = ImageDraw.Draw(im)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                d.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(200, 200, 200))
    return im


def fit(im, w, h, bg=(0, 0, 0), mode="contain"):
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return canvas


def arrow(d, x, y, w=34, color=LINE):
    d.line((x, y, x + w - 10, y), fill=color, width=2)
    d.polygon([(x + w, y), (x + w - 11, y - 6), (x + w - 11, y + 6)], fill=color)


def card(d, box, fill=PANEL, outline=LINE, r=14):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=1)


def label(d, xy, text, size=17, fill=DIM, bold=False):
    d.text(xy, text, font=f(size, bold), fill=fill)


def main():
    W, H = 1680, 1210
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((48, 34), "她到底是怎么动起来的", font=f(38, True), fill=TEXT)
    d.text((48, 86), "三套完全不同的机制，不是一个引擎", font=f(20), fill=DIM)

    # ---------------------------------------------------------------- A
    top = 140
    card(d, (40, top, W - 40, top + 196))
    d.text((70, top + 22), "A  大形态：一张静态原画 + 每帧算一个 transform", font=f(24, True), fill=TEXT)
    lines = [
        "没有骨骼、没有动画文件。浏览器每帧执行一次：",
        "scaleY(1 + 0.0045·sin(1.45t))  →  呼吸（纵向轻微伸缩）",
        "rotate(0.42°·sin(0.36t))       →  站立时的左右轻晃",
        "translateX(...)                →  拖动、点地板走路",
        "所以她的“动”是整张图在动，五官是死的。",
    ]
    for i, t in enumerate(lines):
        d.text((70, top + 62 + i * 28), t, font=f(17), fill=TEXT if i == 0 else DIM)

    big = Image.open(ROOT / "reference/live_big_crop.png").convert("RGB")
    img.paste(fit(big, 165, 200, bg=PANEL), (W - 280, top + 5))

    # ---------------------------------------------------------------- B
    top = 380
    card(d, (40, top, W - 40, top + 500))
    d.text((70, top + 22), "B  小形态：预渲染的透明视频，按状态换片源", font=f(24, True), fill=TEXT)
    d.text((70, top + 58), "不是实时渲染，是 5 分钟烘一条 5 秒的片子，循环播放。", font=f(17), fill=DIM)

    sy = top + 110           # stage row top
    sh = 190                 # stage height
    stages = []

    # 1 source art
    art = Image.open(ROOT / "reference/q_front_cutout.png").convert("RGBA")
    st = fit(art, 150, sh - 10, bg=PANEL)
    stages.append(("原画（1 张）", st, "官方 Q 版正面"))

    # 2 green canvas
    green = Image.open("/Users/orlando/Documents/默认项目/ComfyUI/input/q_loop_test.png").convert("RGB")
    st = fit(green, 240, sh - 10, bg=PANEL)
    stages.append(("合成绿幕", st, "864×480，#00FF00"))

    # 3 generated frames
    frames, _ = read_frames(ROOT / "reference/h3_loop_test.mp4")
    strip = Image.new("RGB", (100 * 3, 64), PANEL)
    for k, idx in enumerate((0, 31, 62)):
        fr = Image.fromarray(frames[idx]).resize((100, 64), Image.LANCZOS)
        strip.paste(fr, (k * 100, 0))
    strip = fit(strip, 300, sh - 30, bg=PANEL)
    stages.append(("模型生成 124 帧", strip, "MiniMax-H3 FL2V"))

    # 4 alpha
    rgb, a = key_frame(frames[31])
    rgba = np.dstack([rgb, a])
    cut = Image.fromarray(rgba, "RGBA").crop((300, 60, 570, 450)).resize((118, 170), Image.LANCZOS)
    chk = checker(118, 170)
    chk.paste(cut, (0, 0), cut)
    stages.append(("抠像出 alpha", chk, "只抠绿，蓝发保住"))

    # 5 video in page
    live = Image.open(ROOT / "reference/live_own_1.png").convert("RGB").crop((820, 380, 1030, 700))
    stages.append(("网页里播放", fit(live, 150, sh - 10, bg=PANEL), "<video> + VP9 alpha"))

    x = 70
    widths = [150, 250, 300, 160, 150]
    for (title, im, sub), wd in zip(stages, widths):
        d.text((x, sy - 26), title, font=f(16, True), fill=ACCENT)
        box = (x, sy, x + wd, sy + sh)
        card(d, box)
        img.paste(im, (x + (wd - im.width) // 2, sy + 6))
        label(d, (x, sy + sh + 8), sub, 15)
        x += wd + 46
        if x < W - 80:
            arrow(d, x - 38, sy + sh // 2)

    # loop row
    ly = sy + sh + 46
    d.text((70, ly), "循环怎么接上：首帧和尾帧喂同一张图，模型必须回到起点", font=f(17), fill=TEXT)
    loop_a = Image.fromarray(frames[0]).resize((210, 118), Image.LANCZOS)
    loop_b = Image.fromarray(frames[-1]).resize((210, 118), Image.LANCZOS)
    img.paste(loop_a, (70, ly + 34))
    img.paste(loop_b, (70 + 210 + 130, ly + 34))
    d.text((70 + 210 + 44, ly + 78), "==", font=f(26, True), fill=WARM)
    label(d, (70, ly + 156), "frame 0", 14)
    label(d, (70 + 210 + 130, ly + 156), "frame 123", 14)
    d.text((70 + 210 + 130 + 240, ly + 60), "alpha 完全一致 → 肉眼无缝",
           font=f(17), fill=DIM)

    # ---------------------------------------------------------------- C
    top = 950
    card(d, (40, top, W - 40, top + 196))
    d.text((70, top + 22), "C  嘴为什么会动", font=f(24, True), fill=TEXT)
    rows = [
        ("图片模式", "WebAudio AnalyserNode 取波形 RMS，超过阈值就显示张嘴图层，90ms 抖一次"),
        ("视频模式", "嘴型烘在视频里；TTS 一响就换到 talk.webm 循环，音频结束再切回 idle"),
        ("共同点", "嘴型都不对应具体音节，只是“像在说话”的节奏"),
    ]
    for i, (k, v) in enumerate(rows):
        y = top + 66 + i * 40
        d.text((70, y), k, font=f(17, True), fill=ACCENT if i < 2 else WARM)
        d.text((190, y), v, font=f(17), fill=DIM)

    d.text((48, H - 44), "本地生成 · 无云端 · 无骨骼 · 帧率 24fps", font=f(15), fill=(110, 104, 124))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, quality=95)
    print(OUT, img.size)


if __name__ == "__main__":
    main()
