using UnityEngine;
using VRM;

namespace Hippocampus.Shell
{
    /// <summary>
    /// Thin expression layer over UniVRM 0.x. The server sends an expression
    /// name; this maps it to the four VRM presets and keeps blinking alive.
    /// </summary>
    public class VrmAvatar : MonoBehaviour
    {
        public Animator animator;
        public VRMBlendShapeProxy proxy;
        public bool relaxArms = true;

        private Vector3 baseScale;
        private float breath;

        private void Awake()
        {
            if (animator == null)
            {
                animator = GetComponentInChildren<Animator>();
            }
            if (proxy == null)
            {
                proxy = GetComponentInChildren<VRMBlendShapeProxy>();
            }
            if (proxy != null && proxy.GetComponent<Blinker>() == null)
            {
                var blinker = proxy.gameObject.AddComponent<Blinker>();
                blinker.Interval = 4.5f;
            }
            baseScale = transform.localScale;
            if (relaxArms)
            {
                RelaxArms();
            }
            SetExpression("neutral");
        }

        private void Update()
        {
            // Subtle breathing so a still frame does not look like a mannequin.
            breath += Time.deltaTime;
            var factor = 1f + Mathf.Sin(breath * 1.6f) * 0.004f;
            transform.localScale = new Vector3(
                baseScale.x, baseScale.y * factor, baseScale.z);
        }

        private void RelaxArms()
        {
            if (animator == null || !animator.isHuman)
            {
                return;
            }
            var leftUpper = animator.GetBoneTransform(HumanBodyBones.LeftUpperArm);
            var rightUpper = animator.GetBoneTransform(HumanBodyBones.RightUpperArm);
            var leftLower = animator.GetBoneTransform(HumanBodyBones.LeftLowerArm);
            var rightLower = animator.GetBoneTransform(HumanBodyBones.RightLowerArm);
            if (leftUpper != null)
            {
                leftUpper.localRotation *= Quaternion.Euler(0f, 0f, 62f);
            }
            if (rightUpper != null)
            {
                rightUpper.localRotation *= Quaternion.Euler(0f, 0f, -62f);
            }
            if (leftLower != null)
            {
                leftLower.localRotation *= Quaternion.Euler(0f, 0f, 8f);
            }
            if (rightLower != null)
            {
                rightLower.localRotation *= Quaternion.Euler(0f, 0f, -8f);
            }
        }

        public void SetExpression(string name)
        {
            if (proxy == null)
            {
                return;
            }
            Set(BlendShapePreset.Neutral, 0f);
            Set(BlendShapePreset.Joy, 0f);
            Set(BlendShapePreset.Angry, 0f);
            Set(BlendShapePreset.Sorrow, 0f);
            Set(BlendShapePreset.Fun, 0f);

            switch ((name ?? "neutral").ToLowerInvariant())
            {
                case "happy":
                case "joy":
                    Set(BlendShapePreset.Joy, 0.85f);
                    break;
                case "sad":
                case "sorrow":
                    Set(BlendShapePreset.Sorrow, 0.85f);
                    break;
                case "angry":
                    Set(BlendShapePreset.Angry, 0.8f);
                    break;
                case "shy":
                case "fun":
                    Set(BlendShapePreset.Fun, 0.7f);
                    break;
                default:
                    Set(BlendShapePreset.Neutral, 1f);
                    break;
            }
        }

        private void Set(BlendShapePreset preset, float value)
        {
            proxy.ImmediatelySetValue(preset, value);
        }
    }
}
