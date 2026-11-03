using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace Hippocampus.Shell
{
    /// <summary>Minimal chat panel: transcript on the left, input at the bottom.</summary>
    public class ChatUI : MonoBehaviour
    {
        public Text output;
        public InputField input;
        public Button send;

        private readonly List<string> lines = new List<string>();

        private void Awake()
        {
            if (send != null)
            {
                send.onClick.AddListener(Submit);
            }
        }

        private void Update()
        {
            if (input != null && input.isFocused &&
                (Input.GetKeyDown(KeyCode.Return) || Input.GetKeyDown(KeyCode.KeypadEnter)))
            {
                Submit();
            }
        }

        public void Submit()
        {
            if (input == null)
            {
                return;
            }

            var text = (input.text ?? string.Empty).Trim();
            if (text.Length == 0)
            {
                return;
            }

            input.text = string.Empty;
            Add("你：" + text);

            var client = GetComponent<CompanionClient>();
            if (client == null)
            {
                client = FindObjectOfType<CompanionClient>();
            }
            if (client != null)
            {
                client.SendText(text);
            }
            input.ActivateInputField();
        }

        public void AddUser(string text)
        {
            Add("你：" + text);
        }

        public void AddBot(string text)
        {
            Add("桃子：" + text);
        }

        public void AddSystem(string text)
        {
            Add("· " + text);
        }

        public void SetStatus(string text)
        {
            AddSystem(text);
        }

        private void Add(string line)
        {
            lines.Add(line);
            if (lines.Count > 120)
            {
                lines.RemoveAt(0);
            }
            if (output != null)
            {
                output.text = string.Join("\n", lines);
            }
        }
    }
}
