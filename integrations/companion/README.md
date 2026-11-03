# Companion shell (A0) · 已暂停

> 2026-11-03 暂停。复盘见 Obsidian `unity使用/`。代码和协议保留。

两个进程：

```
integrations/companion/server.py        大脑服务，WS 6200
integrations/companion/unity/...        Unity 壳
```

## 启动大脑

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

## 启动壳

```bash
open integrations/companion/unity/HippocampusShell/Build/HippocampusShell.app
```

重新编译见 `unity/README.md`。

## 当前能力

- VRM 角色（AvatarSample_A），正面站立，会呼吸、眨眼
- 客厅场景：沙发、电视、窗外风景、地毯、茶几、落地灯、绿植
- 文字对话，1–2 个气泡
- 动作：走过去 / 坐下 / 站起来 / 跳 / 看她 / 挥手 / 点头 / 摇头
- 和 QQ 共用 `self:taozi` 的记忆设计（同一个 Brain 类，独立 DB）
- 每 60 秒睡眠巩固

## 明确没有

- TTS / 口型（表情已接 Joy/Sorrow/Angry/Fun）
- 麦克风输入
- 多房间 / NavMesh
