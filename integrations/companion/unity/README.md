# Unity shell (A0)

一个最小的 Unity 壳：房间、角色占位、聊天框、WebSocket 协议客户端。
角色现在是一个胶囊，先验证「说得通、动得起来、协议不返工」；VRM 换模型是
下一步，不动协议。

## 本机已安装

```
Unity Hub    ~/Applications/Unity Hub.app                      3.3.7
Editor       ~/Applications/Unity/Hub/Editor/2022.3.62f3c1/   2022.3.62f3c1
Project      integrations/companion/unity/HippocampusShell
```

## 一次性授权

Unity 必须有许可证才能编译。打开 Unity Hub：

1. 登录 Unity ID（中国版用手机号 / Unity 中国账号）
2. 偏好设置 → 许可证 → 添加 → 获取免费个人许可证
3. 确认 `~/Library/Application Support/Unity` 或
   `/Library/Application Support/Unity` 下出现 `Unity_lic.ulf`

之后所有编译都可以无头执行。

## 编译

```bash
UNITY="$HOME/Applications/Unity/Hub/Editor/2022.3.62f3c1/Unity.app/Contents/MacOS/Unity"

# 1. 导入 + 编译脚本（同时生成场景）
"$UNITY" -batchmode -nographics -quit \
  -projectPath "$PWD" \
  -executeMethod Hippocampus.Shell.EditorTools.SceneBuilder.BuildScene \
  -logFile -

# 2. 打包 macOS
"$UNITY" -batchmode -nographics -quit \
  -projectPath "$PWD" \
  -executeMethod Hippocampus.Shell.EditorTools.BuildScript.BuildMac \
  -logFile -
```

产物：

```
Build/HippocampusShell.app
```

## 运行

先启动大脑服务（下一步实现，端口 6200）：

```bash
python -m integrations.companion.server
```

再打开 `Build/HippocampusShell.app`。默认连：

```
ws://127.0.0.1:6200/ws
```

## 协议 v1

客户端 → 服务端：

```json
{"type":"hello","user_id":"local","user_name":"你","scene":{...}}
{"type":"user_text","text":"你好","place":"living"}
```

服务端 → 客户端：

```json
{"type":"welcome","character":"桃子姐姐","voice":{"format":"pcm","sample_rate":24000}}
{"type":"turn","turn_id":"t1",
 "say":[{"text":"在。咋了","emotion":"neutral"}],
 "expression":"neutral",
 "actions":[{"id":"a1","action":"look_at","target":"user"}]}
{"type":"error","message":"..."}
```

注意：动作字段同时接受 `action`（JsonUtility 可读）和文档里的 `do`。
服务端两个都发，Unity 侧读 `action`。

## 动作词表

```
walk_to / run_to   target: place id | object id | user
sit                target: 有 sit 能力的物体
stand_up
jump
turn_to / look_at  target: object id | user
wave / nod / shake_head
```

服务端必须先按 `hello.scene` 校验：不在词表、目标不存在、客户端没声明的
动作，直接丢掉，不下发。

## 为什么先不用 VRM

- 协议、动作、场景、UI 先跑通，换模型只改渲染层
- UniVRM / three-vrm 的导入和授权是另一条链路，不该卡住编译
- 胶囊能验证「她转过来看你、走过去坐下、挥手」，这些才是壳的核心

换 VRM 时只替换 `Taozhi` 这个 GameObject，保留 `AvatarController` 的
`avatar` 引用和动作语义。

## 已知限制

- 还没有 TTS（先文字）
- 还没有表情 / 口型
- 没有 NavMesh，走路是直线 `MoveTowards`；换 VRM 后再上寻路
- 场景由 `Assets/Editor/SceneBuilder.cs` 生成，不要手改 `Main.unity`
