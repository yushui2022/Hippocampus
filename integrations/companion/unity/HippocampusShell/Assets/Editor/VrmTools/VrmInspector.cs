using UnityEditor;
using UnityEngine;
using VRM;

namespace Hippocampus.Shell.EditorTools
{
    public static class VrmInspector
    {
        public static void Inspect()
        {
            const string path = "Assets/Models/AvatarSample_A.vrm";
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Debug.Log("VRM_ASSET=" + (asset != null ? asset.name : "null"));
            var all = AssetDatabase.LoadAllAssetsAtPath(path);
            foreach (var item in all)
            {
                Debug.Log("SUB=" + item.GetType().FullName + " " + item.name);
            }
            if (asset == null)
            {
                return;
            }
            var animator = asset.GetComponentInChildren<Animator>();
            Debug.Log("ANIMATOR=" + (animator != null) +
                      " humanoid=" + (animator != null && animator.isHuman));
            var proxy = asset.GetComponentInChildren<VRMBlendShapeProxy>();
            Debug.Log("PROXY=" + (proxy != null));
            if (proxy != null && proxy.BlendShapeAvatar != null)
            {
                foreach (var clip in proxy.BlendShapeAvatar.Clips)
                {
                    Debug.Log("CLIP=" + clip.BlendShapeName +
                              " preset=" + clip.Preset);
                }
            }
            var meta = asset.GetComponentInChildren<VRMMeta>();
            if (meta != null && meta.Meta != null)
            {
                Debug.Log("META=" + meta.Meta.Title + " / " + meta.Meta.Author);
            }
        }
    }
}
