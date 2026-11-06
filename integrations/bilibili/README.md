# Bilibili 弹幕接入

把直播间弹幕送到 `live2d/stream` 的弹幕桥（`:6220`），不依赖 B 站开放平台。

## 为什么不直接用官方轮子

B 站现在有两条限制，旧版 `blivedm` 直连会失败：

| 现象 | 原因 | 这里的处理 |
| --- | --- | --- |
| `-352` | `getDanmuInfo` 需要 **WBI 签名** | `danmaku.py` 自己算 `w_rid` |
| 连接后立刻断开 | 认证包的 `uid` 必须是登录账号 | 启动时用 cookie 查 `nav` 拿 mid |

## 准备

1. 浏览器登录 bilibili.com
2. 复制 `SESSDATA`（DevTools → Application → Cookies → bilibili.com）
3. 写到本目录的 `cookie.txt`，内容一行：

```text
SESSDATA=xxxx%2Cxxxx%2Cxxxx
```

也可以用环境变量 `BILI_COOKIE`。

## 运行

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python danmaku.py 14308771      # 你的直播间号
```

直播间号在直播间地址 `live.bilibili.com/<room_id>` 里。

## 只转发聊天

礼物、进场、点赞、榜单全部丢弃，只把 `弹幕` 和 `醒目留言` POST 到桥。

## 注意

- 房间没开播时也能连上，但不会有消息
- SESSDATA 有有效期，过期会重新 `-352`，换一个就行
- 这是按 B 站网页端协议实现的，平台改版可能失效
