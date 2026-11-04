# 大肥鱼 · 弹幕直播

抖音弹幕进来 → 大脑回话 → 本机 TTS 开口 → 网页形象出镜 → OBS 推给直播伴侣。

## 结构

```text
弹幕来源（抖音/手动）
      │  POST /comment  {"user": "...", "text": "..."}
      ▼
stream/bridge.py  :6220        过滤 · 去重 · 排队 · 限速
      │  WS ws://127.0.0.1:6200/ws
      ▼
Hippocampus Brain（Docker, :6200）
      │  {type:"turn", say:[...]}
      ▼
stream/stream.html             字幕 + 切到 talk 动画 + 播放 TTS
```

## 起服务

```bash
# 1. 大脑（Docker，已有）
docker compose -f qq-stack/compose.yml up -d companion

# 2. 弹幕桥
cd live2d/stream
./.venv/bin/python bridge.py          # :6220

# 3. 静态页面（项目根目录起，已有）
cd live2d && python3 -m http.server 6210
```

打开：

- 直播画面 <http://127.0.0.1:6210/stream/stream.html>
- 场控面板 <http://127.0.0.1:6210/stream/control.html>

## 喂弹幕

```bash
# 单条
./.venv/bin/python feed.py "你好呀"
./.venv/bin/python feed.py --user 小明 "今天播多久"

# 管道：把抓弹幕程序的控制台输出直接灌进来
your-danmu-fetcher | ./.venv/bin/python feed.py --stdin
```

`feed.py --stdin` 能识别这种格式：`【聊天msg】昵称：内容`，其余行按纯文本处理。

**本仓库不包含抖音抓弹幕的实现。** 抓取依赖平台签名逆向，随时可能失效，也不在官方授权范围内；正式接入走抖音开放平台直播玩法（只推送事先配置的评论指令）。

## 场景

| 名字 | 画面 | 触发 |
|---|---|---|
| `idle` | 待机循环 | 默认 |
| `talk` | 说话循环 | 收到回复时自动，音频结束切回 |
| `work` | 摸鱼 | 场控手动 |

```bash
curl -X POST http://127.0.0.1:6220/scene -H 'Content-Type: application/json' -d '{"name":"work"}'
```

## OBS

- 源类型：**浏览器源**，不是窗口采集（要透明必须用浏览器源）
- 地址填 `http://127.0.0.1:6210/stream/stream.html`
- 分辨率 1920×1080，帧率 30
- 勾选「关闭源时关闭页面」「刷新浏览器源」
- 声音：TTS 从网页播，浏览器源里勾「通过 OBS 控制音频」

## 合规

- 画面常驻「AI 实时生成」标识（已做在右上角）
- 抖音要求直播中有真人出镜，长时间只有画面会被限流
- 形象是 CC BY-NC-SA 4.0 社区二创：**禁止商用**，收礼、带货、广告都算商用
- 必须署名：上善无形（原 OC 溟月）、ZipZipPipe（女仆二创）、这个刀子真甜（Q 版）

## 环境

`bridge.py` 需要 `aiohttp`，已经建好独立 venv：

```bash
cd live2d/stream && ./.venv/bin/pip install -r requirements.txt
```
