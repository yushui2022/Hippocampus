using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Hippocampus.Shell.EditorTools
{
    public static class BuildScript
    {
        private const string ScenePath = "Assets/Scenes/Main.unity";

        [MenuItem("Hippocampus/Build macOS")]
        public static void BuildMac()
        {
            if (!File.Exists(ScenePath))
            {
                SceneBuilder.BuildScene();
            }

            Directory.CreateDirectory("Build");
            var options = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = Path.GetFullPath("Build/HippocampusShell.app"),
                target = BuildTarget.StandaloneOSX,
                options = BuildOptions.None
            };

            var report = BuildPipeline.BuildPlayer(options);
            var summary = report.summary;
            if (summary.result != BuildResult.Succeeded)
            {
                Debug.LogError(
                    "Build failed: " + summary.result +
                    " errors=" + summary.totalErrors);
                EditorApplication.Exit(1);
                return;
            }

            Debug.Log(
                "Build ok: " + summary.outputPath +
                " size=" + summary.totalSize);
            EditorApplication.Exit(0);
        }

        public static void BuildAll()
        {
            SceneBuilder.BuildScene();
            BuildMac();
        }
    }
}
