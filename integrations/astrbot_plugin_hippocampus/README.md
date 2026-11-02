# astrbot_plugin_hippocampus

AstrBot 插件：用 [Hippocampus](../../README.md) 内核提供长期记忆。

- 写入：`on_llm_request` / `on_llm_response` 捕获对话，按话题切 episode
- 召回：`on_llm_request` 检索，作为临时片段注入 `extra_user_content_parts`
- 记忆：分级清晰度（L0–L4）、活跃年龄、事实 TTL、主动提及预算
- 模型：本地 Ollama `bge-m3`（向量）+ DeepSeek（第一人称摘要），都可换成 OpenAI 兼容接口

## 安装

插件目录放在 AstrBot 的 `data/plugins/` 下，并把 Hippocampus 仓库挂载到容器：

```yaml
volumes:
  - ../astrbot_plugin_hippocampus:/AstrBot/data/plugins/astrbot_plugin_hippocampus
  - ../Hippocampus:/opt/hippocampus:ro
environment:
  - HIPPOCAMPUS_PATH=/opt/hippocampus
  - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
```

在 AstrBot WebUI 的「插件」页重载即可。

## 配置

见 `_conf_schema.json`。默认：

- 向量：`http://host.docker.internal:11434/v1` + `bge-m3`
- 摘要：DeepSeek `deepseek-chat`，key 读 `DEEPSEEK_API_KEY`
- 不配向量也能跑，退化为 BM25
- 摘要失败自动退回首句截断

## 命令

```
/hmem                 状态
/hmem recall <query>  手动召回
/hmem flush           把当前 episode 缓冲写入记忆
```

## 注意

- 只捕获“和机器人对话”的消息；群聊里没有 @ 的闲聊不会进入记忆
- 不要把这个插件和 LivingMemory 同时挂到同一会话，否则会注入两份记忆
- QQ 协议端有风控风险，务必使用小号
