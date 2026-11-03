# 桃子姐姐 · 2.5D 网页壳

用本地的 ComfyUI + NoobAI-XL 生成角色，再做成可动的 2.5D 立绘，接 v3 `Brain` 聊天。

> **这不是 Cubism 的 `.moc3`。**
> 真 Live2D 需要 Cubism Editor 导出；本机没装，See-through 拆层也需要 24G NVIDIA。
> 这一版是“分层立绘 + 局部重绘 + 网页合成”，先看效果，再决定要不要投入真 Live2D。

---

## 打开

```bash
cd /Users/orlando/Documents/蓝色大肥鱼/live2d
python3 -m http.server 6210
```

浏览器打开：

```
http://127.0.0.1:6210/web/
```

默认连 `ws://127.0.0.1:6200/ws`（`companion-shell` 容器）。
第一次发消息会写入 `qq-stack/data/companion/brain.db`。

## 文件

```text
live2d/
├── gen_taozi.py               NoobAI 生成角色候选
├── gen_expressions.py         局部重绘：闭眼 / 张嘴
├── build_layers.py            切身体 / 头 / 眼 / 嘴 / 脖子
├── serve.sh                   启动静态服务
├── candidates/                4 个候选图
├── taozi/
│   ├── layer_plan.json        DeepSeek 拆层规划（坐标不可靠，仅参考）
│   ├── first-pass             30MB 的初版 PSD（矩形层，不能直接用）
│   └── parts/                 真正用于网页的分层
├── assets/
│   ├── room.png               客厅背景（NoobAI 生成）
│   ├── taozi.png              角色 base
│   ├── taozi_blink.png        闭眼
│   └── taozi_talk.png         张嘴
├── web/                       index.html / style.css / app.js
└── shot6.png                  最终截图
```

## 角色

- 棕色齐肩短发、粉色卫衣、黑色短裤、过膝袜、粉色运动鞋
- 4 个候选里选了 `seed_1101`：正面、全身、灰底、线条干净
- 表情：闭眼、张嘴用 NoobAI 局部重绘，只改眼睛/嘴，其他像素保持原图
- 背景：NoobAI 生成客厅，绿沙发、木地板、窗、绿植

## 动起来的部分

| 效果 | 做法 |
|---|---|
| 呼吸 | 整体 `scaleY` 正弦 |
| 轻晃 | 整体 `rotate` 正弦 + 鼠标视差 |
| 眨眼 | 每 3–7 秒交叉淡入闭眼图 120ms |
| 说话 | 收到回复时在 base / 张嘴图之间切换 |
| 房间视差 | 背景反向小幅移动 |

这些都在 `web/app.js`，不依赖 Live2D SDK。

## 为什么不用本地那条 image-to-live2d 管线

仓库 `AI皮套/image-to-live2d-demo` 需要：

- Cubism Core（Cubism Editor 自带）
- See-through + 24G NVIDIA GPU
- Live2D Agent Kit + psd2live + JDK 21

本机是 Apple Silicon，没有 NVIDIA；它的 `local_mac` fallback 只用矩形裁切，
每一层都带着整张图，无法动画。所以只借了它的 planner 和 PSD 格式，分层改成
按这张平涂图自己切。

## 表情

四套：`happy / sad / angry / shy`。

- 由 companion 服务返回的 `expression` 字段驱动
- 点击她 → 短暂 happy
- 表情层和口型层分开：说话时嘴仍然动，表情的眼睛还在
- happy / shy 来自局部重绘；angry 来自低强度整图重绘；sad 是重绘的眼部
- 文件：`assets/expr_happy.png` 等

## 声音

Qwen3-TTS 1.7B（MLX 8bit），本地、无 API、Apache 2.0。

```bash
./tts/start.sh          # 常驻服务 :6211
curl http://127.0.0.1:6211/health
./tts/stop.sh
```

- 默认 **Serena · 轻声**
- 温柔 / 俏皮可通过请求的 `mood` 切换
- 网页回复已经接上：收到 `turn` 后请求 `/tts`，用音频包络驱动张嘴
- 4.8 秒语音生成约 **0.95 秒**（常驻、已加载模型）
- 试听：`http://127.0.0.1:6210/tts/`

## 下一步

1. **真 Live2D**：装 Cubism Editor，把 `taozi/parts` 或 `first-pass` 精修成 `.moc3`
3. **共享记忆**：网页和 QQ 指向同一个 `brain.db` + 同一个 `self:taozi`
4. **表情**：再用局部重绘做 happy / sad / angry，而不是只靠眨眼
5. **互动**：点她、拖拽、长时间不说话时自己发呆
6. **专属音色**：用 Qwen3-TTS Base + 3–10 秒参考音克隆

## 授权

- 角色图：本地 NoobAI 生成，无第三方版权
- 房间：本地生成
- 参考的 `image-to-live2d-demo`：MIT（代码），未使用其受限制资产
- 如果以后商用，Cubism Editor / Live2D SDK 的授权要单独确认
