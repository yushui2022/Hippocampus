# HANDOFF · Hippocampus × NapCat（路线 B）

> **Unity 壳已暂停（2026-11-03）。** 复盘、环境、协议、资产授权全部整理在
> `/Users/orlando/Documents/Obsidian Vault/unity使用/`。代码和 6200 服务保留，不再投入。
>
> **2026-11-03 更新：v0.9.0，v3 P1 已完成。**
> 记忆链路已从「v2 内核直连」升级为 `Brain`：感官门控 → 72 小时暂存 →
> 睡眠巩固 → 来源规则 → 人物卡。线上适配器已切到 `Brain`，新的自我 scope
> 是 `self:taozi`，情境是 `qq:dm:<uid>` / `qq:group:<gid>`。
> 旧的 `qq:*` / `napcat:*` 数据仍在同一个库文件里，但新代码不再读取。
> 根测试 77 项，适配器测试 14 项，`eval.forgetting_v3` 五项全过。

> 写给接手的人 / 另一个 AI。目标是让你在 10 分钟内知道：
> 这是什么、现在到哪了、怎么跑、哪里会踩坑、下一步该做什么。

---

## 1. 一句话

**NapCat 是耳朵和嘴，Hippocampus 是脑子。**

QQ 消息经 NapCat 进入 `hippocampus-napcat` 适配器；对话历史、长期记忆、自我认知、提示词、回复节奏全部由 Hippocampus 这一侧拥有。AstrBot 不在回复链路里。

---

## 2. 当前状态（2026-11-03）

| 项 | 状态 |
|---|---|
| 仓库 | `github.com/yushui2022/Hippocampus` |
| 分支 / 提交 | `v3.0` / 见 `git log`（`main` 稍后同步） |
| 版本 | `v0.9.0` |
| 根测试 | 77 个 unittest 全绿 |
| 适配器测试 | 14 个 pytest 全绿 |
| 运行中 | `hippocampus-bridge`、`napcat` |
| bridge 健康 | `{"ok": true, "napcat": true}` |
| 当前记忆库 | 旧数据保留；新 scope `self:taozi` 从空开始 |
| 真实群消息 | 新适配器 scope `qq:GroupMessage:922410298` 已写入 |
| 图片 | DeepSeek Flash 多模态直传，已直接 API 验证 |
| 搜索 | `web_search` / `web_fetch`，已合成链路验证 |
| 人格 | SelfModel 已生成（含 `qq:` scope） |

### 重要：两代 scope 并存

| scope / 情境 | 来源 | 状态 |
|---|---|---|
| `self:taozi` | v0.9.0 `Brain`（现行） | 新记忆全部写这里 |
| `qq:dm:<uid>` / `qq:group:<gid>` | 上面这个 Brain 的情境 id | 不是独立记忆，只是来源标签 |
| `qq:GroupMessage:*`、`napcat:*` | v0.8 及更早 | 历史数据，保留但不再读取 |

v3 是「一个自我一个大脑」：私聊和群聊共享记忆，靠来源规则决定能不能说。
旧数据没有 v3 的 `source` 字段，所以即使被读到也会被视为可说；但新 scope
不会去读它们。确认不需要后，可以按 v3 文档第 15 节直接删除旧 scope。

---

## 3. 架构

```text
QQ
 ↓
NapCat（OneBot v11 协议端，只负责收/发/登录）
 ↓  ws://astrbot:6199/ws
hippocampus-napcat
 ├─ OneBot 事件解析 / action 请求
 ├─ 每 scope 对话历史（group/private 独立）
 ├─ Hippocampus
 │   ├─ engrams（记忆）
 │   ├─ facts（事实 / TTL）
 │   ├─ recall / tick / consolidate
 │   └─ SelfModel（性格 / 关系 / 梗 / 说话习惯）
 ├─ DeepSeek Flash（文本 + 图片 + tool calling）
 └─ web_search / web_fetch
```

### 一次消息的闭环

```text
message event
  → 解析 / 去重
  → 判断是否值得记（私聊 / 测试群 / 被叫到）
  → MemoryService.record → feed_episode
  → 每 60 秒 flush → engram
  → LLM 摘要 → gist / topics / importance
  → 下一轮 recall → 注入 <长期记忆>
  → SelfModel 注入
  → DeepSeek Flash（可调 web tools）
  → 清理 + 1~3 条气泡
  → 助手回复写回记忆
  → 每 60 秒 tick / consolidate / reflect
```

---

## 4. 仓库地图

```text
Hippocampus/
├── hippocampus/                 # 记忆内核
│   ├── kernel.py                # 公开 API
│   ├── decay.py                 # R / S / fidelity
│   ├── models.py                # Event / Engram / render
│   ├── store.py                 # SQLite / vectors / facts / migrations
│   ├── retrieval/               # BM25 / vectors / RRF / MMR
│   ├── facts.py                 # 规则事实抽取 + TTL
│   ├── summarizer.py            # LLM 摘要 + AsyncSummarizer
│   ├── self_model.py            # 性格 / 关系 / 梗
│   ├── episode.py               # 话题切分
│   └── observatory.py           # 只读观测台
├── integrations/
│   ├── napcat/                  # ★ 当前正式适配器
│   │   ├── hippocampus_napcat/
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   ├── compose.example.yml
│   │   └── README.md
│   └── qq_bridge/               # 旧单文件原型，已弃用
├── tests/                       # 59 个根测试
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INVARIANTS.md
│   ├── STATUS.md
│   ├── SUMMARIZER.md
│   └── PERFORMANCE.md
├── README.md
├── CHANGELOG.md
└── HANDOFF.md                   # 本文件
```

设计文档在 Obsidian：

```text
/Users/orlando/Documents/Obsidian Vault/情感陪伴记忆系统/
├── 设计-海马记忆系统（Hippocampus）.md
├── 执行-海马记忆系统 v2（融合 LivingMemory）.md
├── 对比-海马记忆系统 vs LivingMemory.md
├── 开源拆解-大肥鱼记忆系统（LivingMemory）.md
└── 海马记忆系统参考开源技术项目.md
```

---

## 5. 适配器模块职责

| 文件 | 职责 |
|---|---|
| `config.py` | 所有环境变量、capture 策略 |
| `events.py` | OneBot v11 事件解析、`MessageEvent`、主动插话判断 |
| `onebot.py` | 反向 WS 连接、`send_group_msg` / `send_private_msg`、action 响应 |
| `memory.py` | 持有 Store / Embedder / Summarizer / Hippocampus / SelfModel，record/recall/tick/reflect |
| `chat.py` | 对话历史、system prompt、memory/self 注入、DeepSeek 工具循环、多气泡节奏 |
| `tools.py` | `web_search`（Bing + DDG lite）、`web_fetch`、SSRF 防护 |
| `bridge.py` | 事件编排、capture 策略、`/hmem` 命令、maintenance |
| `main.py` | aiohttp `/ws`、`/health`、`/status`、startup/cleanup |

---

## 6. 部署

部署目录在仓库外：

```text
/Users/orlando/Documents/蓝色大肥鱼/qq-stack/
├── compose.yml
├── .env              # 含 DeepSeek key，勿提交
├── data/             # AstrBot 数据 + hippocampus/memory.db
├── napcat/config/    # OneBot WS 指向 ws://astrbot:6199/ws
└── ntqq/             # QQ 登录态
```

服务：

```yaml
napcat:
  image: mlikiowa/napcat-docker:latest
  environment: [MODE=astrbot]

bridge:
  image: soulter/astrbot:latest      # 仅借用 Python 3.12 + aiohttp
  entrypoint: ["python", "-u", "-m", "hippocampus_napcat"]
  environment:
    PYTHONPATH=/opt/hippocampus:/opt/hippocampus/integrations/napcat
    MEMORY_DB=/data/hippocampus/memory.db
    DEEPSEEK_API_KEY=...
  volumes:
    - ./data:/data
    - ../Hippocampus:/opt/hippocampus:ro
  networks:
    astrbot_network:
      aliases: [astrbot]             # NapCat 的 WS host
```

关键点：

- NapCat 的 OneBot WS 仍固定指向 `ws://astrbot:6199/ws`
- bridge 在 compose 网络里用 alias `astrbot`
- 旧 AstrBot 服务仍留在 compose 里，但已停止；不要和 bridge 同时占用 alias
- `MEMORY_DB` 指向 `/data/hippocampus/memory.db`

### 启动 / 登录

```bash
cd /Users/orlando/Documents/蓝色大肥鱼/qq-stack
docker compose up -d bridge napcat
docker logs -f hippocampus-bridge
```

NapCat 首次/登录态失效会打印二维码：

```bash
docker logs napcat | grep 二维码
docker cp napcat:/app/napcat/cache/qrcode.png ./qrcode.png
open ./qrcode.png
```

**Docker Desktop 重启后，NapCat 登录态可能失效，需要重新扫码。** 这是 QQ 协议端限制，不是 bridge 的问题。

扫码若报：

```text
Login Error ... serverErrorCode: 168
你的账号近期存在安全风险
```

需要先在手机 QQ 解除账号风控，或换一个干净小号。频繁重扫会加重风控。

---

## 7. 配置

全部环境变量，见 `integrations/napcat/README.md` 的完整表。最常用：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MEMORY_DB` | `/data/hippocampus/memory.db` | 记忆库 |
| `DEEPSEEK_API_KEY` | 空 | 必填 |
| `CHAT_MODEL` | `deepseek-flash` | 多模态 |
| `EMBED_BASE` | `http://host.docker.internal:11434/v1` | 本地 bge-m3 |
| `BOT_NAMES` | `桃子姐姐,桃子姐` | 名字触发 |
| `TEST_GROUP` | `922410298` | 主动插话测试群 |
| `CAPTURE_GROUPS` | 同测试群 | 记录全部闲聊的群 |
| `ACTIVE_CHANCE` | `0.25` | 主动插话 |
| `NAME_CHANCE` | `0.95` | 被叫名字回复 |
| `RECALL_BUDGET` | `2` | 自动召回条数 |
| `WEB_TOOLS` | `true` | 搜索开关 |

---

## 8. 命令与测试

### 运行测试

```bash
# 根内核
cd Hippocampus
python3 -m unittest discover -s tests -p 'test_*.py'

# 适配器（容器里有 aiohttp / pytest）
docker exec -e PYTHONPATH=/opt/hippocampus:/opt/hippocampus/integrations/napcat \
  hippocampus-bridge pytest -q -p no:cacheprovider \
  /opt/hippocampus/integrations/napcat/tests
```

### 健康检查

```bash
docker exec hippocampus-bridge python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:6199/health').read().decode())"
```

### QQ 命令

```text
/hmem               记忆状态
/hmem flush         立即落盘缓冲
/hmem recall 关键词  手动召回
/hmem self          查看自我认知
/hmem reflect       强制反思
```

### 观测台

```bash
cd Hippocampus
python3 -m hippocampus.observatory \
  --db /Users/orlando/Documents/蓝色大肥鱼/qq-stack/data/hippocampus/memory.db \
  --port 8765
```

打开 `http://127.0.0.1:8765`，只读。

---

## 9. 已验证 / 未验证

### 已验证

- OneBot 事件解析、@ / 图片 / 自己消息过滤
- 私聊消息写入记忆
- 无向量模型时 BM25 写入 + 召回
- `/hmem` 命令
- DeepSeek Flash 图片识别
- Bing 搜索 + DDG lite 兜底
- web_fetch SSRF 防护（localhost / 内网拒绝）
- 工具调用循环（搜索两次 + 抓网页）
- 合成闭环：写入“冰美式” → 新进程空历史 → 仍能召回
- 真实 QQ 链路：bridge 连接、`qq:` scope 写入

### 未验证 / 风险

- 连续 24 小时稳定性、掉线重连
- 多群并发下的 SQLite / worker 竞争
- 图片从 QQ URL 到 DeepSeek 的真实成功率
- Bing 被限流后的搜索质量
- 记忆质量：测试群仍会记录不少低价值闲聊
- S4（该不该说）/ S6（情绪延续）没有完整实现
- 没有真人盲测、真实日志 holdout

---

## 10. 已知问题与优先级

### P0：接手先做

1. **清理/迁移旧 scope**
   - 旧 `napcat:*` 数据 65 条，新 `qq:*` 28 条
   - 决定保留、迁移或删除，避免观测台混淆

2. **写入质量**
   - 测试群仍会记“嗯 / 666 / 扫荡给什么”
   - 建议加：最小字数、语气词过滤、importance 下限

3. **给新适配器加 recv/reply 日志**
   - 当前只记录连接
   - 出问题只能靠 DB 反推，调试慢

4. **真实 QQ 图片链路**
   - 需要实测 NapCat 图片 URL 能否被 bridge 下载

### P1

- 话题分段：现在一条 episode 可能包含多件事
- 去重/合并：相似记忆会重复
- 把 `memory_search` / `memory_remember` 做成模型工具（当前 adapter 只有 web tools，自动召回为主）
- 事实抽取：当前规则版，线上只有 3 条 facts
- SelfModel 证据门槛：防止一句玩笑固化性格

### P2

- `salient()` 主动提及（当前主动插话是概率，不是记忆驱动）
- 话题冷却 / 提及预算
- 图谱记忆（`enable_graph`）
- 观测台编辑/删除
- 真实日志回放 + 真人盲测

---

## 11. 硬约束（不要违反）

1. **AstrBot 不回到回复链路。**
   它的历史、persona、工具、空 @ 提示词会再次抢上下文。

2. **NapCat 只做协议。**
   不要往 NapCat 配置里塞人格或记忆。

3. **Hippocampus 拥有记忆和人格。**
   不要引入 Pi / Mem0 / 其他 agent runtime 当底座；要加也只能做工具层。

4. **事实只从用户话语来。**
   AI 自己的回复可以进 episode，但不能产生 fact。

5. **`fidelity` 只增不减。**
   召回可以提升可及性，不能恢复已丢失的细节；用户重新复述是例外。

6. **不要提交密钥。**
   `.env`、`data/`、`ntqq/`、`*.db` 都不进仓库。

---

## 12. 调试手册

| 现象 | 先看 |
|---|---|
| 没回复 | `docker logs hippocampus-bridge`；`/health`；NapCat 是否 `napcat connected` |
| 消息没进 bridge | NapCat 日志 `接收 <-`；OneBot WS 地址 |
| 搜索不工作 | bridge 日志 `tool web_search`；容器能否访问 Bing |
| 图片看不到 | `CHAT_MODEL=deepseek-flash`；图片 URL 能否下载 |
| 记忆不增长 | `CAPTURE_GROUPS`；`/hmem flush`；DB `engrams` |
| 人格不更新 | `/hmem reflect`；`DEEPSEEK_API_KEY` |
| 扫描登录失败 168 | 账号风控，手机 QQ 解封或换号 |
| Docker 重启后掉登录 | 重新扫码 |

---

## 13. 下一步建议

如果只能做一件事：**先清掉旧 `napcat:*` scope，并给新适配器加写入过滤。**

理由：现在不是“没有记忆”，是“记忆里混了太多不值得记的东西”。先把写入质量做对，再谈人格和主动表达。

---

## 14. 交接 checklist

- [ ] 能跑根测试 59 项
- [ ] 能跑适配器测试 12 项
- [ ] 知道 `qq:` 与 `napcat:` scope 的区别
- [ ] 知道 AstrBot 已停止、bridge 用 alias `astrbot`
- [ ] 知道 NapCat 登录态可能失效、需要扫码
- [ ] 知道 `.env` 和 `data/` 不能提交
- [ ] 知道当前记忆质量的主要问题是“写入太宽”
- [ ] 知道下一步优先做写入过滤 + 日志
