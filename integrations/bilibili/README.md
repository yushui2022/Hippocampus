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

## 开播（5000 粉以下）

B 站从 2025-05-26 起把第三方开播门槛提到 **5000 粉**，不够的话网页上
不会显示推流地址。`go_live.py` 走官方直播姬的 `platform=pc_link` 通道
申请地址，和直播姬客户端做的是同一件事：

```bash
# cookie.txt 需要 SESSDATA 和 bili_jct 两项
python go_live.py --cookie cookie.txt
python go_live.py --cookie cookie.txt --stop   # 结束直播
```

输出里是 OBS 的「服务器」和「串流密钥」。这是绕过平台限制的做法，
账号风险自行判断；粉丝够 5000 的话直接在网页开播设置里复制即可。
