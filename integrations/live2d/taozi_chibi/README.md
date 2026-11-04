# 本地自产 Q 版动画流水线

把一张 Q 版立绘变成透明 WebM 待机 / 说话 / 反应动画。全程本地，不依赖外部 API。

## 为什么用 MiniMax-H3 而不是 Wan

机器上没有 Wan 权重，但已经装好并跑通 **MiniMax-H3 FL2VA**（首帧 + 尾帧 → 视频）：

- 首帧 = 尾帧 = 同一张图 → 天然无缝循环
- 10 秒、带音频、24fps
- 4 步 turbo LoRA，5 秒 864×480 约 260–300 秒

Wan 2.2 要另下约 35 GB，且没有首尾帧约束，循环接缝要自己处理。

## 流程

```text
Q 版立绘 (reference/q_front_cutout.png)
  → 合成到纯绿 #00FF00 画布，人物占 70% 高、脚底 88%
  → MiniMax-H3 FL2V，首帧=尾帧，4 步 turbo LoRA，cfg 1.0
  → HSV 色相抠像（只抠绿，蓝发不被吃）
  → 剪影遮罩兜底（背景被模型脑补时用）
  → kill_green 最后一遍清绿边
  → 统一缩放 640×360、VP9 alpha WebM
```

## 命令

```bash
# 1. 出片（ComfyUI 需要在 8188 跑着）
python taozi_chibi/h3_clip.py \
  --name idle --prompt taozi_chibi/prompts/idle_v3.txt \
  --seed 551027 --steps 4 --cfg 1.0

# 2. 抠像 + 打包
python taozi_chibi/h3_to_webm.py reference/h3_idle_v3.mp4 taozi_chibi/final \
  --name idle --height 360

# 背景被脑补时，加剪影遮罩（box = 人物在输入画布里的位置）
python taozi_chibi/h3_to_webm.py reference/h3_loop_test.mp4 taozi_chibi/final \
  --name idle --height 360 \
  --matte reference/q_front_cutout.png --matte-box 325,86,539,422
```

## 参数经验

| 参数 | 结论 |
|---|---|
| cfg | **必须 1.0**。1.5 能把背景压住，但眼睛会发紫、蕾丝糊掉 |
| steps | 4（turbo LoRA 训练步数） |
| 尺寸 | 864×480 起步；768p 更清晰但更慢 |
| 首尾帧 | 必须同一张图，否则循环会跳 |
| 提示词 | 绿幕要写成"用于抠像的素材"，并逐条禁掉人脸/半身像/图案 |

## 已知问题

- 5 秒后模型容易在绿幕里"脑补"一张巨大的脸。两种解法：换更硬的提示词，或用剪影遮罩硬切。
- 生成版比原画略软（锐度约为原图 66%），46vh 显示尺寸下看不出来。
- 说话时嘴型是模型自己动的，不对应具体音节；TTS 播完就切回待机。

## 输入尺寸约定

`MiniMaxH3ImageToVideo` 的 `first_frame` 是**直接拉伸**到画布的，所以输入图必须已经是目标画幅比例（16:9），否则人物会被压扁。
