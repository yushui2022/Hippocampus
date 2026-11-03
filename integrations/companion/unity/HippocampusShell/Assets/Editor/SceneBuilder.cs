using System.IO;
using Hippocampus.Shell;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

namespace Hippocampus.Shell.EditorTools
{
    /// <summary>
    /// Generates the playable scene from code so the repository stays reviewable
    /// and the scene can be rebuilt after a merge without hand-editing YAML.
    /// </summary>
    public static class SceneBuilder
    {
        private const string ScenePath = "Assets/Scenes/Main.unity";
        private const string MaterialDir = "Assets/Materials";

        [MenuItem("Hippocampus/Rebuild Main Scene")]
        public static void RebuildMenu()
        {
            BuildScene();
        }

        public static void BuildScene()
        {
            var scene = EditorSceneManager.NewScene(
                NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");

            var peach = MakeMaterial("Peach", new Color(1f, 0.72f, 0.66f));
            var floorMaterial = MakeMaterial("Floor", new Color(0.22f, 0.24f, 0.28f));
            var wood = MakeMaterial("Wood", new Color(0.45f, 0.32f, 0.22f));
            var fabric = MakeMaterial("Fabric", new Color(0.35f, 0.42f, 0.55f));
            var green = MakeMaterial("Plant", new Color(0.28f, 0.55f, 0.34f));

            var cameraGo = new GameObject(
                "Main Camera", typeof(Camera), typeof(AudioListener));
            cameraGo.tag = "MainCamera";
            cameraGo.transform.position = new Vector3(0f, 1.9f, -4.8f);
            cameraGo.transform.LookAt(new Vector3(0f, 1.0f, 1.0f));
            var camera = cameraGo.GetComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.07f, 0.08f, 0.11f);

            var lightGo = new GameObject("Directional Light", typeof(Light));
            var light = lightGo.GetComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.1f;
            lightGo.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

            var floor = Primitive(
                PrimitiveType.Plane, "Floor",
                new Vector3(0f, 0f, 2f), new Vector3(2.2f, 1f, 2.2f), floorMaterial);
            floor.GetComponent<Renderer>().sharedMaterial = floorMaterial;

            var sofa = Primitive(
                PrimitiveType.Cube, "Sofa",
                new Vector3(3f, 0.5f, 2f), new Vector3(2.2f, 1f, 1f), fabric);
            sofa.GetComponent<Renderer>().sharedMaterial = fabric;

            var chair = Primitive(
                PrimitiveType.Cube, "GiftChair",
                new Vector3(-2.5f, 0.5f, 1.5f), new Vector3(1f, 1f, 1f), wood);
            chair.GetComponent<Renderer>().sharedMaterial = wood;

            var plant = Primitive(
                PrimitiveType.Cylinder, "Plant",
                new Vector3(4.5f, 0.9f, 3.8f), new Vector3(0.5f, 1.6f, 0.5f), green);
            plant.GetComponent<Renderer>().sharedMaterial = green;

            var avatarGo = Primitive(
                PrimitiveType.Capsule, "Taozhi",
                new Vector3(0f, 1f, 0f), new Vector3(0.8f, 1f, 0.8f), peach);
            avatarGo.GetComponent<Renderer>().sharedMaterial = peach;
            Object.DestroyImmediate(avatarGo.GetComponent<CapsuleCollider>());

            var living = new GameObject("place_living");
            living.transform.position = new Vector3(0f, 0f, 0f);
            var balcony = new GameObject("place_balcony");
            balcony.transform.position = new Vector3(6f, 0f, 4f);

            var avatar = avatarGo.AddComponent<AvatarController>();
            avatar.avatar = avatarGo.transform;
            avatar.sofa = sofa.transform;
            avatar.chairGift = chair.transform;
            avatar.plant = plant.transform;
            avatar.living = living.transform;
            avatar.balcony = balcony.transform;

            // -- UI ------------------------------------------------------------
            var canvasGo = new GameObject(
                "Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            var canvas = canvasGo.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = canvasGo.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1280f, 720f);
            scaler.matchWidthOrHeight = 0.5f;

            if (Object.FindObjectOfType<EventSystem>() == null)
            {
                new GameObject(
                    "EventSystem", typeof(EventSystem), typeof(StandaloneInputModule));
            }

            var outputGo = new GameObject(
                "Output", typeof(RectTransform), typeof(Text));
            outputGo.transform.SetParent(canvasGo.transform, false);
            var outputRect = outputGo.GetComponent<RectTransform>();
            outputRect.anchorMin = new Vector2(0f, 0.22f);
            outputRect.anchorMax = new Vector2(1f, 1f);
            outputRect.offsetMin = new Vector2(24f, 8f);
            outputRect.offsetMax = new Vector2(-24f, -24f);
            var output = outputGo.GetComponent<Text>();
            output.font = font;
            output.fontSize = 20;
            output.color = Color.white;
            output.alignment = TextAnchor.LowerLeft;
            output.horizontalOverflow = HorizontalWrapMode.Wrap;
            output.verticalOverflow = VerticalWrapMode.Truncate;
            output.supportRichText = false;
            output.text = "桃子姐姐\n\n（左下角输入，回车发送）";

            var input = MakeInput(canvasGo.transform, font);
            var send = MakeButton(canvasGo.transform, font);

            var chat = canvasGo.AddComponent<ChatUI>();
            chat.output = output;
            chat.input = input;
            chat.send = send;

            var companionGo = new GameObject("Companion");
            var client = companionGo.AddComponent<CompanionClient>();
            client.chat = chat;
            client.avatar = avatar;

            Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[]
            {
                new EditorBuildSettingsScene(ScenePath, true)
            };
            AssetDatabase.SaveAssets();
            Debug.Log("Scene built: " + ScenePath);
        }

        private static GameObject Primitive(
            PrimitiveType type, string name, Vector3 position, Vector3 scale,
            Material material)
        {
            var go = GameObject.CreatePrimitive(type);
            go.name = name;
            go.transform.position = position;
            go.transform.localScale = scale;
            var renderer = go.GetComponent<Renderer>();
            if (renderer != null)
            {
                renderer.sharedMaterial = material;
            }
            return go;
        }

        private static Material MakeMaterial(string name, Color color)
        {
            Directory.CreateDirectory(MaterialDir);
            var path = MaterialDir + "/" + name + ".mat";
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (material == null)
            {
                material = new Material(Shader.Find("Standard"));
                material.color = color;
                AssetDatabase.CreateAsset(material, path);
            }
            return material;
        }

        private static Text MakeText(
            Transform parent, string name, string value, int size, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Text));
            go.transform.SetParent(parent, false);
            var rect = go.GetComponent<RectTransform>();
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(14f, 6f);
            rect.offsetMax = new Vector2(-14f, -6f);
            var text = go.GetComponent<Text>();
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.fontSize = size;
            text.color = color;
            text.alignment = TextAnchor.MiddleLeft;
            text.supportRichText = false;
            text.text = value;
            return text;
        }

        private static InputField MakeInput(Transform parent, Font font)
        {
            var go = new GameObject(
                "Input", typeof(RectTransform), typeof(Image), typeof(InputField));
            go.transform.SetParent(parent, false);
            var rect = go.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(0f, 0f);
            rect.anchorMax = new Vector2(1f, 0f);
            rect.pivot = new Vector2(0.5f, 0f);
            rect.offsetMin = new Vector2(24f, 24f);
            rect.offsetMax = new Vector2(-160f, 76f);

            var image = go.GetComponent<Image>();
            image.color = new Color(0.12f, 0.13f, 0.16f, 0.95f);

            var text = MakeText(go.transform, "Text", "", 20, Color.white);
            text.font = font;

            var placeholder = MakeText(
                go.transform, "Placeholder", "说点什么…", 20,
                new Color(1f, 1f, 1f, 0.35f));
            placeholder.font = font;

            var field = go.GetComponent<InputField>();
            field.textComponent = text;
            field.placeholder = placeholder;
            field.targetGraphic = image;
            field.lineType = InputField.LineType.SingleLine;
            field.characterLimit = 200;
            return field;
        }

        private static Button MakeButton(Transform parent, Font font)
        {
            var go = new GameObject(
                "Send", typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            var rect = go.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(1f, 0f);
            rect.anchorMax = new Vector2(1f, 0f);
            rect.pivot = new Vector2(1f, 0f);
            rect.sizeDelta = new Vector2(112f, 52f);
            rect.anchoredPosition = new Vector2(-24f, 24f);

            var image = go.GetComponent<Image>();
            image.color = new Color(1f, 0.62f, 0.55f, 0.95f);

            var label = MakeText(go.transform, "Label", "发送", 20, Color.black);
            label.font = font;
            label.alignment = TextAnchor.MiddleCenter;

            return go.GetComponent<Button>();
        }
    }
}
