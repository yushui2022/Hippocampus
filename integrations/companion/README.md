# Companion brain（:6200）

只负责记忆、人格、上下文和模型调用。画面在
[dafeiyu-live](https://github.com/yushui2022/dafeiyu-live)，通过 WebSocket 连这里。

```text
integrations/companion/server.py   大脑服务，WS :6200
```

## 启动

```bash
cd /Users/orlando/Documents/蓝色大肥鱼/qq-stack
docker compose up -d companion
curl http://127.0.0.1:6200/health
```

或用本机 Python（3.10+，需要 aiohttp）：

```bash
python integrations/companion/server.py \
  --env ../qq-stack/.env \
  --db ../qq-stack/data/companion/brain.db
```

需要 `DEEPSEEK_API_KEY`。

## 协议

```text
→ {"type":"hello","user_id":"stream","user_name":"观众","scene":{...}}
→ {"type":"user_text","text":"在吗"}
← {"type":"turn","say":[{"text":"..."}],"expression":"happy",
   "actions":[],"looking_at":""}
```

画面侧怎么用见 dafeiyu-live 的 `docs/LIVE.md`。

## 能力

- 记忆：感官门控 → 72 小时暂存 → 睡眠巩固 → 证据门控自我模型
- 人格：嘴硬心软，对方示弱/难过时先解决问题
- 回复：最多两个气泡，一次 20 多字
- 动作：`wave / nod / shake_head / jump / look_at`
- 自动上网：安静时每 15 秒换页，每 4 页嘟囔一句

## 和直播的分工

| 这里 | dafeiyu-live |
| --- | --- |
| 记忆、人格、模型调用 | 画面、动作素材、TTS、弹幕、推流 |
| `:6200` | `:6210` / `:6211` / `:6220` |
