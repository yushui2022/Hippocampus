# Unity shell (A0)

一个最小的 Unity 壳：房间、VRM 角色、聊天框、WebSocket 协议客户端。
角色是 VRoid 官方的 `AvatarSample_A`，表情走 VRM 0.x 的 BlendShape 预设。

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

# 2. 重建场景 + 打包 macOS（推荐，省一次 Unity 启动）
"$UNITY" -batchmode -nographics \
  -projectPath "$PWD" \
  -executeMethod Hippocampus.Shell.EditorTools.BuildScript.BuildAll \
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

## VRM

- 引擎：UniVRM 0.131.4（`Packages/com.vrmc.univrm`、`com.vrmc.gltf`）
- 模型：`Assets/Models/AvatarSample_A.prefab`（VRoid 官方样例）
- 源文件：`/Users/orlando/Documents/蓝色大肥鱼/vrm/AvatarSample_A.vrm`
- 授权：VRoid 样例，个人/商用可用、无需署名；不能转售 VRM 文件本身
- 色彩空间：Linear（MToon 在 Gamma 下会发灰）
- 表情：`VrmAvatar.SetExpression()` 映射 `neutral / happy / sad / angry / shy`
  到 `Neutral / Joy / Sorrow / Angry / Fun`
- 眨眼：UniVRM 的 `Blinker`，约 4.5 秒一次
- 待机：呼吸缩放 + 手臂从 T-pose 放下

换模型时保持 `AvatarController.avatar` 指向根节点，协议和动作不用改。

## 已知限制

- 还没有 TTS（先文字）
- 表情已接，口型还没有
- 没有 NavMesh，走路是直线 `MoveTowards`
- 场景由 `Assets/Editor/SceneBuilder.cs` 生成，不要手改 `Main.unity`
- Unity 2022 在 macOS 上批处理退出偶尔会挂；`BuildAll` 里显式
  `EditorApplication.Exit(0)`，如果进程不退，杀掉并删 `Temp/UnityLockfile` 即可
