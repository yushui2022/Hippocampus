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
            var floorMaterial = MakeMaterial("Floor", new Color(0.30f, 0.21f, 0.15f));
            var wood = MakeMaterial("Wood", new Color(0.38f, 0.26f, 0.18f));
            var fabric = MakeMaterial("Fabric", new Color(0.33f, 0.40f, 0.48f));
            var green = MakeMaterial("Plant", new Color(0.24f, 0.48f, 0.30f));
            var wall = MakeMaterial("Wall", new Color(0.84f, 0.80f, 0.74f));
            var rug = MakeMaterial("Rug", new Color(0.52f, 0.27f, 0.23f));
            var cushion = MakeMaterial("Cushion", new Color(0.78f, 0.73f, 0.64f));
            var tvBody = MakeMaterial("TV", new Color(0.04f, 0.04f, 0.05f));
            var tvScreen = MakeMaterial("Screen", new Color(0.10f, 0.20f, 0.28f));
            tvScreen.EnableKeyword("_EMISSION");
            tvScreen.SetColor("_EmissionColor", new Color(0.16f, 0.40f, 0.55f) * 1.6f);
            var sky = MakeMaterial("Sky", new Color(0.49f, 0.70f, 0.88f));
            var hill = MakeMaterial("Hill", new Color(0.22f, 0.36f, 0.24f));
            var sunMat = MakeMaterial("Sun", new Color(1f, 0.92f, 0.70f));
            sunMat.EnableKeyword("_EMISSION");
            sunMat.SetColor("_EmissionColor", new Color(1f, 0.85f, 0.55f) * 2f);
            var frameMat = MakeMaterial("Frame", new Color(0.72f, 0.70f, 0.66f));

            var cameraGo = new GameObject(
                "Main Camera", typeof(Camera), typeof(AudioListener));
            cameraGo.tag = "MainCamera";
            cameraGo.transform.position = new Vector3(0f, 1.25f, -2.35f);
            cameraGo.transform.LookAt(new Vector3(0f, 1.02f, 0f));
            var camera = cameraGo.GetComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.05f, 0.06f, 0.08f);

            var lightGo = new GameObject("Directional Light", typeof(Light));
            var light = lightGo.GetComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.0f;
            lightGo.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.36f, 0.34f, 0.32f);

            var fillGo = new GameObject("WarmFill", typeof(Light));
            var fill = fillGo.GetComponent<Light>();
            fill.type = LightType.Point;
            fill.color = new Color(1f, 0.86f, 0.70f);
            fill.intensity = 1.3f;
            fill.range = 10f;
            fillGo.transform.position = new Vector3(0f, 2.6f, 2.2f);

            // -- room shell -----------------------------------------------------
            Cube("Floor", new Vector3(0f, -0.05f, 1.5f),
                 new Vector3(12f, 0.1f, 11f), floorMaterial);
            Cube("Ceiling", new Vector3(0f, 3.25f, 1.5f),
                 new Vector3(12f, 0.1f, 11f), wall);
            Cube("BackWallLeft", new Vector3(-3.95f, 1.6f, 5.6f),
                 new Vector3(3.3f, 3.2f, 0.16f), wall);
            Cube("BackWallRight", new Vector3(3.95f, 1.6f, 5.6f),
                 new Vector3(3.3f, 3.2f, 0.16f), wall);
            Cube("BackWallBelow", new Vector3(0f, 0.45f, 5.6f),
                 new Vector3(4.6f, 0.9f, 0.16f), wall);
            Cube("BackWallAbove", new Vector3(0f, 3.05f, 5.6f),
                 new Vector3(4.6f, 0.3f, 0.16f), wall);
            Cube("WallLeft", new Vector3(-5.6f, 1.6f, 1.5f),
                 new Vector3(0.16f, 3.2f, 8.4f), wall);
            Cube("WallRight", new Vector3(5.6f, 1.6f, 1.5f),
                 new Vector3(0.16f, 3.2f, 8.4f), wall);

            // -- window + scenery ----------------------------------------------
            Cube("WindowFrameL", new Vector3(-2.3f, 1.9f, 5.5f),
                 new Vector3(0.1f, 2.1f, 0.24f), frameMat);
            Cube("WindowFrameR", new Vector3(2.3f, 1.9f, 5.5f),
                 new Vector3(0.1f, 2.1f, 0.24f), frameMat);
            Cube("WindowFrameB", new Vector3(0f, 0.9f, 5.5f),
                 new Vector3(4.7f, 0.1f, 0.24f), frameMat);
            Cube("WindowFrameT", new Vector3(0f, 2.9f, 5.5f),
                 new Vector3(4.7f, 0.1f, 0.24f), frameMat);
            Cube("WindowMullion", new Vector3(0f, 1.9f, 5.5f),
                 new Vector3(0.06f, 2.0f, 0.2f), frameMat);

            Cube("SkyBackdrop", new Vector3(0f, 4f, 9.5f),
                 new Vector3(18f, 9f, 0.2f), sky);
            Sphere("HillA", new Vector3(-3f, 1.2f, 8.5f),
                   new Vector3(5f, 2.4f, 2.4f), hill);
            Sphere("HillB", new Vector3(3.5f, 1f, 8.8f),
                   new Vector3(4f, 2f, 2f), hill);
            Sphere("Sun", new Vector3(3.2f, 4.8f, 8.8f),
                   new Vector3(1.2f, 1.2f, 1.2f), sunMat);
            Cylinder("TreeTrunk", new Vector3(-4.2f, 1.2f, 8.2f),
                     new Vector3(0.25f, 1.2f, 0.25f), wood);
            Sphere("TreeCrown", new Vector3(-4.2f, 2.8f, 8.2f),
                   new Vector3(1.6f, 1.4f, 1.6f), green);

            // -- furniture ------------------------------------------------------
            Cube("Rug", new Vector3(0f, 0.02f, 1.2f),
                 new Vector3(4.2f, 0.04f, 3.0f), rug);

            var sofaRoot = new GameObject("Sofa");
            sofaRoot.transform.position = new Vector3(2.9f, 0f, 4.4f);
            ChildCube(sofaRoot.transform, "Base", new Vector3(0f, 0.35f, 0f),
                      new Vector3(2.5f, 0.5f, 1.0f), fabric);
            ChildCube(sofaRoot.transform, "Back", new Vector3(0f, 0.85f, 0.44f),
                      new Vector3(2.5f, 0.9f, 0.22f), fabric);
            ChildCube(sofaRoot.transform, "ArmL", new Vector3(-1.3f, 0.6f, 0f),
                      new Vector3(0.18f, 0.6f, 1.0f), fabric);
            ChildCube(sofaRoot.transform, "ArmR", new Vector3(1.3f, 0.6f, 0f),
                      new Vector3(0.18f, 0.6f, 1.0f), fabric);
            ChildCube(sofaRoot.transform, "CushionL", new Vector3(-0.62f, 0.63f, -0.02f),
                      new Vector3(1.1f, 0.16f, 0.85f), cushion);
            ChildCube(sofaRoot.transform, "CushionR", new Vector3(0.62f, 0.63f, -0.02f),
                      new Vector3(1.1f, 0.16f, 0.85f), cushion);

            var tvRoot = new GameObject("TV");
            tvRoot.transform.position = new Vector3(-2.9f, 0f, 4.5f);
            ChildCube(tvRoot.transform, "Cabinet", new Vector3(0f, 0.28f, 0f),
                      new Vector3(2.1f, 0.55f, 0.5f), wood);
            ChildCube(tvRoot.transform, "Body", new Vector3(0f, 1.15f, 0.05f),
                      new Vector3(1.8f, 1.0f, 0.12f), tvBody);
            ChildCube(tvRoot.transform, "Screen", new Vector3(0f, 1.15f, -0.02f),
                      new Vector3(1.62f, 0.82f, 0.02f), tvScreen);

            Cube("CoffeeTableTop", new Vector3(0f, 0.42f, 1.3f),
                 new Vector3(1.8f, 0.08f, 0.9f), wood);
            Cube("CoffeeTableLegA", new Vector3(-0.75f, 0.21f, 0.95f),
                 new Vector3(0.08f, 0.42f, 0.08f), wood);
            Cube("CoffeeTableLegB", new Vector3(0.75f, 0.21f, 0.95f),
                 new Vector3(0.08f, 0.42f, 0.08f), wood);
            Cube("CoffeeTableLegC", new Vector3(-0.75f, 0.21f, 1.65f),
                 new Vector3(0.08f, 0.42f, 0.08f), wood);
            Cube("CoffeeTableLegD", new Vector3(0.75f, 0.21f, 1.65f),
                 new Vector3(0.08f, 0.42f, 0.08f), wood);

            var chairRoot = new GameObject("GiftChair");
            chairRoot.transform.position = new Vector3(-4.0f, 0f, 2.0f);
            chairRoot.transform.rotation = Quaternion.Euler(0f, 20f, 0f);
            ChildCube(chairRoot.transform, "Seat", new Vector3(0f, 0.45f, 0f),
                      new Vector3(0.65f, 0.1f, 0.65f), cushion);
            ChildCube(chairRoot.transform, "Back", new Vector3(0f, 0.85f, -0.3f),
                      new Vector3(0.65f, 0.8f, 0.08f), cushion);
            for (var i = 0; i < 4; i++)
            {
                var sx = i % 2 == 0 ? -0.26f : 0.26f;
                var sz = i < 2 ? -0.26f : 0.26f;
                ChildCube(chairRoot.transform, "Leg" + i, new Vector3(sx, 0.22f, sz),
                          new Vector3(0.06f, 0.44f, 0.06f), wood);
            }

            var plantRoot = new GameObject("Plant");
            plantRoot.transform.position = new Vector3(4.8f, 0f, 4.8f);
            ChildCylinder(plantRoot.transform, "Pot", new Vector3(0f, 0.25f, 0f),
                          new Vector3(0.55f, 0.5f, 0.55f), wood);
            ChildSphere(plantRoot.transform, "Leaves", new Vector3(0f, 1.2f, 0f),
                        new Vector3(1.0f, 1.2f, 1.0f), green);

            var lampRoot = new GameObject("Lamp");
            lampRoot.transform.position = new Vector3(-4.8f, 0f, 4.5f);
            ChildCylinder(lampRoot.transform, "Pole", new Vector3(0f, 0.9f, 0f),
                          new Vector3(0.06f, 1.8f, 0.06f), tvBody);
            ChildCylinder(lampRoot.transform, "Shade", new Vector3(0f, 1.85f, 0f),
                          new Vector3(0.5f, 0.35f, 0.5f), sunMat);

            var avatarPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(
                "Assets/Models/AvatarSample_A.prefab");
            GameObject avatarGo;
            if (avatarPrefab != null)
            {
                avatarGo = (GameObject)PrefabUtility.InstantiatePrefab(avatarPrefab);
                avatarGo.name = "Taozhi";
                avatarGo.transform.position = Vector3.zero;
                avatarGo.transform.rotation = Quaternion.Euler(0f, 180f, 0f);
            }
            else
            {
                avatarGo = Primitive(
                    PrimitiveType.Capsule, "Taozhi",
                    new Vector3(0f, 1f, 0f), new Vector3(0.8f, 1f, 0.8f), peach);
                avatarGo.GetComponent<Renderer>().sharedMaterial = peach;
                Object.DestroyImmediate(avatarGo.GetComponent<CapsuleCollider>());
            }

            var vrm = avatarGo.GetComponent<VrmAvatar>();
            if (vrm == null)
            {
                vrm = avatarGo.AddComponent<VrmAvatar>();
            }

            var living = new GameObject("place_living");
            living.transform.position = new Vector3(0f, 0f, 0f);
            var balcony = new GameObject("place_balcony");
            balcony.transform.position = new Vector3(3f, 0f, 5.2f);

            var avatar = avatarGo.AddComponent<AvatarController>();
            avatar.avatar = avatarGo.transform;
            avatar.sofa = sofaRoot.transform;
            avatar.chairGift = chairRoot.transform;
            avatar.plant = plantRoot.transform;
            avatar.living = living.transform;
            avatar.balcony = balcony.transform;
            avatar.sitDrop = avatarPrefab != null ? 0f : 0.45f;

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
            client.vrm = vrm;

            Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[]
            {
                new EditorBuildSettingsScene(ScenePath, true)
            };
            AssetDatabase.SaveAssets();
            Debug.Log("Scene built: " + ScenePath);
        }

        private static GameObject Cube(
            string name, Vector3 position, Vector3 scale, Material material)
        {
            return Primitive(PrimitiveType.Cube, name, position, scale, material);
        }

        private static GameObject Sphere(
            string name, Vector3 position, Vector3 scale, Material material)
        {
            return Primitive(PrimitiveType.Sphere, name, position, scale, material);
        }

        private static GameObject Cylinder(
            string name, Vector3 position, Vector3 scale, Material material)
        {
            return Primitive(PrimitiveType.Cylinder, name, position, scale, material);
        }

        private static GameObject ChildPrimitive(
            Transform parent, PrimitiveType type, string name, Vector3 localPosition,
            Vector3 localScale, Material material)
        {
            var go = GameObject.CreatePrimitive(type);
            go.name = name;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPosition;
            go.transform.localScale = localScale;
            var renderer = go.GetComponent<Renderer>();
            if (renderer != null)
            {
                renderer.sharedMaterial = material;
            }
            return go;
        }

        private static GameObject ChildCube(
            Transform parent, string name, Vector3 localPosition,
            Vector3 localScale, Material material)
        {
            return ChildPrimitive(
                parent, PrimitiveType.Cube, name, localPosition, localScale, material);
        }

        private static GameObject ChildSphere(
            Transform parent, string name, Vector3 localPosition,
            Vector3 localScale, Material material)
        {
            return ChildPrimitive(
                parent, PrimitiveType.Sphere, name, localPosition, localScale, material);
        }

        private static GameObject ChildCylinder(
            Transform parent, string name, Vector3 localPosition,
            Vector3 localScale, Material material)
        {
            return ChildPrimitive(
                parent, PrimitiveType.Cylinder, name, localPosition, localScale, material);
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
