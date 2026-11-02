# Hippocampus × NapCat（路线 B）

> **NapCat 是耳朵和嘴，Hippocampus 是脑子。**
> AstrBot 不在这条链路里。

这是一个完全围绕 Hippocampus 设计的 NapCat 适配器，不是“插件挂在别人框架上”的临时方案。

## 链路

```
QQ
 ↓
NapCat（OneBot v11 协议端）
 ↓  ws://bridge:6199/ws
hippocampus-napcat
 ├─ OneBot 事件 / 发送
 ├─ 对话历史（每群 / 每人独立）
 ├─ 海马记忆（写入 / 召回 / tick / consolidate）
 ├─ SelfModel（性格 / 关系 / 梗 / 说话习惯）
 ├─ DeepSeek Flash（文本 + 图片）
 └─ web_search / web_fetch
```

所有上下文、人格、回忆、回复节奏都由本项目决定。NapCat 不参与 prompt，也没有第二套记忆。

## 特性

- 每个群 / 私聊独立 `scope`
- 群聊环境只记录“值得记”的内容，不把大群闲聊灌进记忆库
- 默认：测试群记录全部；其他群只在被叫到 / @ 时记录
- 自动召回 2 条 + SelfModel 注入
- 每 60 秒 `tick` / `consolidate` / 反思
- 名字触发 95%；指定群 25% 主动插话
- 单句 / 连发 2–3 条 / 偶尔小作文
- “你好”走条件反射，不经过大模型，不会长篇客服腔
- 空 @ 接上一句，不问“有什么事”
- 图片直接走 DeepSeek Flash 多模态
- 自建搜索：Bing 主路 + DuckDuckGo lite 兜底，带 SSRF 防护
- `/hmem` 状态、flush、recall、self、reflect
- `/health`、`/status` 供外部监控

## 快速开始

### 方式一：复用现有 qq-stack

`compose.yml` 里的 `bridge` 服务只需要：

```yaml
entrypoint: ["python", "-u", "-m", "hippocampus_napcat"]
environment:
  - PYTHONPATH=/opt/hippocampus:/opt/hippocampus/integrations/napcat
volumes:
  - ../Hippocampus:/opt/hippocampus:ro
```

NapCat 继续连 `ws://astrbot:6199/ws`（bridge 在 compose 网络里用 `astrbot` 作为 alias）。

### 方式二：独立 compose

```bash
cp .env.example .env
docker compose -f compose.example.yml up -d
```

## 配置

所有配置都来自环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `BRIDGE_HOST` | `0.0.0.0` | 监听地址 |
| `BRIDGE_PORT` | `6199` | OneBot 反向 WS 端口 |
| `MEMORY_DB` | `/data/hippocampus/memory.db` | 记忆库 |
| `DEEPSEEK_API_KEY` | 空 | 对话 + 摘要 |
| `CHAT_MODEL` | `deepseek-flash` | 多模态对话模型 |
| `CHAT_BASE` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `SUMMARY_MODEL` | `deepseek-chat` | 记忆摘要 |
| `EMBED_BASE` | `http://host.docker.internal:11434/v1` | bge-m3 |
| `EMBED_MODEL` | `bge-m3` | 向量模型 |
| `BOT_NAMES` | `桃子姐姐,桃子姐` | 名字触发 |
| `TEST_GROUP` | `922410298` | 主动插话的测试群 |
| `CAPTURE_GROUPS` | 同 `TEST_GROUP` | 记录全部闲聊的群 |
| `CAPTURE_PRIVATE` | `true` | 私聊是否记录 |
| `ACTIVE_CHANCE` | `0.25` | 主动插话概率 |
| `NAME_CHANCE` | `0.95` | 被叫名字时回复概率 |
| `RECALL_BUDGET` | `2` | 自动召回条数 |
| `WEB_TOOLS` | `true` | 搜索 / 抓网页 |
| `MULTI_CHANCE` | `0.40` | 拆成多条的概率 |
| `ESSAY_CHANCE` | `0.12` | 小作文概率 |
| `MAINTENANCE_SECONDS` | `60` | tick / 反思周期 |

## 记忆闭环

```
收到消息
  → 判断是否值得记（私聊 / 测试群 / 被叫到）
  → feed_episode 缓冲
  → 每 60 秒 flush → engram
  → LLM 摘要 → gist / topics / importance
  → 下一轮 recall → 注入 <长期记忆>
  → 每 4 条未反思记忆 → SelfModel 更新
  → 每 60 秒 tick / consolidate → 老化 / 事实过期
```

## 命令

```
/hmem               记忆状态
/hmem flush         立即落盘当前缓冲
/hmem recall 关键词  手动召回
/hmem self          查看自我认知
/hmem reflect       强制反思，更新人格
```

## 测试

```bash
PYTHONPATH=../..:. pytest -q tests
```

覆盖：

- OneBot 事件解析（私聊 / 群聊 / @ / 图片 / 自己）
- 主动插话只发生在测试群
- 打招呼 / AI 识别 / 多气泡切分
- SSRF 防护
- 无向量模型时的写入 + 召回
- `/hmem` 命令
- 私聊消息写入记忆

## 故障排查

| 现象 | 检查 |
|---|---|
| bridge 没有 `napcat connected` | NapCat 的 WS 地址是否为 `ws://astrbot:6199/ws` |
| 收到消息但不回 | `/health`、bridge 日志、DeepSeek key |
| 搜索失败 | 容器能否访问 Bing / DuckDuckGo |
| 图片看不到 | `CHAT_MODEL` 是否为 `deepseek-flash`，图片 URL 是否可下载 |
| 记忆不增长 | `/hmem flush`；`CAPTURE_GROUPS` 是否包含当前群 |
| 人格不更新 | `/hmem reflect`；`DEEPSEEK_API_KEY` 是否有效 |

## 设计边界

- 不引入 AstrBot 的 agent / 历史 / persona
- 不引入 Pi / 其他 agent runtime 作为底座
- NapCat 只负责协议
- Hippocampus 拥有记忆、人格、上下文
- 搜索和图片是工具，不参与记忆归属
