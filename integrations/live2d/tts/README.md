# 本地 TTS · Qwen3-TTS

用 Apple MLX 在 M5 Max 上跑 Qwen3-TTS 1.7B，本地、无 API、Apache 2.0。

## 试听

```
http://127.0.0.1:6210/tts/
```

4 个样本：

- `samples/serena_soft_000.wav` — Serena，轻声，**当前默认**
- `samples/serena_warm_000.wav` — Serena，温柔（可选）
- `samples/serena_plain_000.wav` — Serena，模型默认
- `samples/vivian_lively_000.wav` — Vivian，俏皮

## 模型

```
mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit
```

- 9 个预置音色；中文女声是 Serena（温柔）和 Vivian（明亮）
- 支持自然语言语气指令（`--instruct`）
- 支持 3 秒音频克隆（需要 Base 模型，另下）
- 24 kHz 单声道

## 命令

```bash
export HF_ENDPOINT=https://hf-mirror.com

tts/.venv/bin/python -m mlx_audio.tts.generate \
  --model mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit \
  --text "你回来啦。今天怎么样？" \
  --voice Serena \
  --instruct "像朋友聊天，语气温柔，不要播音腔" \
  --lang_code zh \
  --output_path tts/samples \
  --file_prefix test \
  --audio_format wav
```

## 速度

在这台 M5 Max 上，约 4 秒的句子：

- 含模型加载：2.6–9.9 秒
- 常驻进程后，生成本身大约 1–3 秒
- 模型支持流式；实时对话需要常驻服务 + `--stream`

对 QQ 语音条完全够；要做到“边说边出”，需要把模型加载一次后常驻。

## 授权

- Qwen3-TTS：Apache 2.0
- 生成的声音用于个人项目没问题；克隆别人声音需要本人授权

## 下一步

1. 你选一个音色
2. 把 TTS 做成常驻 HTTP 服务（`POST /tts` 返回 wav）
3. QQ bridge 回复时按需生成语音条
4. 网页壳加音频播放 + 口型包络
