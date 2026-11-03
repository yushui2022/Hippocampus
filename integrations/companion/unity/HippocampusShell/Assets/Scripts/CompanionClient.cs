using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace Hippocampus.Shell
{
    [Serializable]
    public class TypeOnly
    {
        public string type;
    }

    [Serializable]
    public class SayLine
    {
        public string text;
        public string emotion;
    }

    [Serializable]
    public class WireAction
    {
        public string id;
        public string action;
        public string target;
    }

    [Serializable]
    public class WireTurn
    {
        public string type;
        public string turn_id;
        public SayLine[] say;
        public string expression;
        public WireAction[] actions;
    }

    [Serializable]
    public class WireError
    {
        public string type;
        public string message;
    }

    [Serializable]
    public class HelloMessage
    {
        public string type = "hello";
        public string user_id;
        public string user_name;
        public SceneInfo scene;
        public string[] actions;
    }

    [Serializable]
    public class UserTextMessage
    {
        public string type = "user_text";
        public string text;
        public string place;
    }

    /// <summary>
    /// Protocol v1 client. The shell never decides what to remember and never
    /// talks to a model directly: it sends events and renders the server's turn.
    /// </summary>
    public class CompanionClient : MonoBehaviour
    {
        public string url = "ws://127.0.0.1:6200/ws";
        public string userId = "local";
        public string userName = "你";
        public ChatUI chat;
        public AvatarController avatar;

        private ClientWebSocket socket;
        private CancellationTokenSource cts;
        private readonly ConcurrentQueue<string> incoming = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<Action> mainThread = new ConcurrentQueue<Action>();
        private volatile bool connected;
        private bool connecting;
        private string currentPlace = "living";

        private async void Start()
        {
            await Connect();
        }

        private async Task Connect()
        {
            if (connecting)
            {
                return;
            }
            connecting = true;
            try
            {
                socket = new ClientWebSocket();
                cts = new CancellationTokenSource();
                await socket.ConnectAsync(new Uri(url), cts.Token);
                connected = true;
                RunOnMain(() => chat?.SetStatus("已连接 " + url));
                SendHello();
                _ = ReceiveLoop();
            }
            catch (Exception e)
            {
                RunOnMain(() => chat?.SetStatus("连接失败：" + e.Message));
                await Task.Delay(3000);
                connecting = false;
                _ = Connect();
                return;
            }
            connecting = false;
        }

        private async Task ReceiveLoop()
        {
            var buffer = new byte[16384];
            var stream = new System.IO.MemoryStream();
            try
            {
                while (socket != null && socket.State == WebSocketState.Open)
                {
                    stream.SetLength(0);
                    WebSocketReceiveResult result;
                    do
                    {
                        result = await socket.ReceiveAsync(
                            new ArraySegment<byte>(buffer), cts.Token);
                        if (result.MessageType == WebSocketMessageType.Close)
                        {
                            break;
                        }
                        stream.Write(buffer, 0, result.Count);
                    }
                    while (!result.EndOfMessage);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        break;
                    }
                    incoming.Enqueue(Encoding.UTF8.GetString(stream.ToArray()));
                }
            }
            catch (Exception e)
            {
                RunOnMain(() => chat?.SetStatus("连接断开：" + e.Message));
            }

            connected = false;
            await Task.Delay(3000);
            _ = Connect();
        }

        private void Update()
        {
            while (incoming.TryDequeue(out var payload))
            {
                Handle(payload);
            }
            while (mainThread.TryDequeue(out var action))
            {
                action();
            }
        }

        private void Handle(string payload)
        {
            TypeOnly head;
            try
            {
                head = JsonUtility.FromJson<TypeOnly>(payload);
            }
            catch (Exception)
            {
                return;
            }
            if (head == null)
            {
                return;
            }

            switch (head.type)
            {
                case "welcome":
                    chat?.SetStatus("已进入房间");
                    break;
                case "turn":
                    RenderTurn(JsonUtility.FromJson<WireTurn>(payload));
                    break;
                case "error":
                    var error = JsonUtility.FromJson<WireError>(payload);
                    chat?.AddSystem("服务端错误：" + (error != null ? error.message : ""));
                    break;
            }
        }

        private void RenderTurn(WireTurn turn)
        {
            if (turn == null)
            {
                return;
            }
            if (turn.say != null)
            {
                foreach (var line in turn.say)
                {
                    if (line != null && !string.IsNullOrEmpty(line.text))
                    {
                        chat?.AddBot(line.text);
                    }
                }
            }
            if (turn.actions != null && avatar != null)
            {
                foreach (var action in turn.actions)
                {
                    if (action != null)
                    {
                        avatar.Execute(action.action, action.target);
                    }
                }
            }
        }

        public void SendText(string text)
        {
            if (!connected)
            {
                chat?.AddSystem("还没连上大脑服务。");
                return;
            }
            SendRaw(JsonUtility.ToJson(new UserTextMessage
            {
                text = text,
                place = currentPlace
            }));
        }

        private void SendHello()
        {
            var hello = new HelloMessage
            {
                user_id = userId,
                user_name = userName,
                scene = avatar != null ? avatar.Scene() : new SceneInfo()
            };
            SendRaw(JsonUtility.ToJson(hello));
        }

        private async void SendRaw(string payload)
        {
            try
            {
                if (socket == null || socket.State != WebSocketState.Open)
                {
                    return;
                }
                var bytes = Encoding.UTF8.GetBytes(payload);
                await socket.SendAsync(
                    new ArraySegment<byte>(bytes),
                    WebSocketMessageType.Text,
                    true,
                    cts.Token);
            }
            catch (Exception e)
            {
                RunOnMain(() => chat?.SetStatus("发送失败：" + e.Message));
            }
        }

        private void RunOnMain(Action action)
        {
            mainThread.Enqueue(action);
        }

        private async void OnDestroy()
        {
            try
            {
                cts?.Cancel();
                if (socket != null && socket.State == WebSocketState.Open)
                {
                    await socket.CloseAsync(
                        WebSocketCloseStatus.NormalClosure, "bye",
                        CancellationToken.None);
                }
            }
            catch (Exception)
            {
                // shutting down anyway
            }
            socket?.Dispose();
        }
    }
}
