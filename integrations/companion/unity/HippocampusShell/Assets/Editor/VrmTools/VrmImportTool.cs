using System;
using System.IO;
using UniGLTF;
using UnityEditor;
using UnityEngine;
using VRM;

namespace Hippocampus.Shell.EditorTools
{
    public static class VrmImportTool
    {
        private static string prefabAbsolute;
        private static DateTime deadline;

        public static void ImportAvatar()
        {
            var source = "/Users/orlando/Documents/蓝色大肥鱼/vrm/AvatarSample_A.vrm";
            prefabAbsolute = Path.GetFullPath("Assets/Models/AvatarSample_A.prefab");
            deadline = DateTime.UtcNow.AddMinutes(10);
            var prefab = UnityPath.FromFullpath(prefabAbsolute);
            vrmAssetPostprocessor.ImportVrmAndCreatePrefab(source, prefab);
            EditorApplication.update += WaitForPrefab;
            Debug.Log("IMPORT_STARTED");
        }

        private static void WaitForPrefab()
        {
            if (File.Exists(prefabAbsolute))
            {
                EditorApplication.update -= WaitForPrefab;
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();
                Debug.Log("IMPORTED_EXISTS=True");
                EditorApplication.Exit(0);
                return;
            }
            if (DateTime.UtcNow > deadline)
            {
                EditorApplication.update -= WaitForPrefab;
                Debug.LogError("IMPORTED_EXISTS=False timeout");
                EditorApplication.Exit(1);
            }
        }
    }
}
