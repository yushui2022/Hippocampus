using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace Hippocampus.Shell
{
    [Serializable]
    public class ScenePlace
    {
        public string id;
        public string name;
    }

    [Serializable]
    public class SceneObjectInfo
    {
        public string id;
        public string name;
        public string place;
        public string[] affords;
    }

    [Serializable]
    public class SceneInfo
    {
        public ScenePlace[] places;
        public SceneObjectInfo[] objects;
        public string[] actions;
    }

    /// <summary>
    /// Executes the server's high-level action vocabulary. The model never
    /// touches bones: it says "walk_to sofa", this component does the rest.
    /// </summary>
    public class AvatarController : MonoBehaviour
    {
        public Transform avatar;
        public Transform sofa;
        public Transform chairGift;
        public Transform plant;
        public Transform living;
        public Transform balcony;

        public float walkSpeed = 1.6f;
        public float runSpeed = 3.6f;

        private readonly Dictionary<string, Transform> targets = new Dictionary<string, Transform>();
        private Vector3 destination;
        private bool moving;
        private float speed;
        private string pendingSit;
        private bool sitting;
        private float baseY;

        private void Awake()
        {
            baseY = avatar != null ? avatar.position.y : 1f;
            targets["sofa"] = sofa;
            targets["chair_gift"] = chairGift;
            targets["plant"] = plant;
            targets["living"] = living;
            targets["balcony"] = balcony;
        }

        public SceneInfo Scene()
        {
            return new SceneInfo
            {
                places = new[]
                {
                    new ScenePlace { id = "living", name = "客厅" },
                    new ScenePlace { id = "balcony", name = "阳台" }
                },
                objects = new[]
                {
                    new SceneObjectInfo
                    {
                        id = "sofa", name = "沙发", place = "living",
                        affords = new[] { "sit" }
                    },
                    new SceneObjectInfo
                    {
                        id = "chair_gift", name = "你送的椅子", place = "living",
                        affords = new[] { "sit" }
                    },
                    new SceneObjectInfo
                    {
                        id = "plant", name = "绿萝", place = "balcony",
                        affords = new[] { "look_at" }
                    }
                },
                actions = new[]
                {
                    "walk_to", "run_to", "sit", "stand_up", "jump",
                    "turn_to", "look_at", "wave", "nod", "shake_head"
                }
            };
        }

        public void Execute(string verb, string target)
        {
            if (avatar == null || string.IsNullOrEmpty(verb))
            {
                return;
            }

            switch (verb)
            {
                case "walk_to":
                    MoveTo(target, false, null);
                    break;
                case "run_to":
                    MoveTo(target, true, null);
                    break;
                case "sit":
                    MoveTo(target, false, "sit");
                    break;
                case "stand_up":
                    StandUp();
                    break;
                case "jump":
                    StartCoroutine(JumpRoutine());
                    break;
                case "wave":
                    StartCoroutine(WaveRoutine());
                    break;
                case "nod":
                    StartCoroutine(NodRoutine());
                    break;
                case "shake_head":
                    StartCoroutine(ShakeRoutine());
                    break;
                case "turn_to":
                case "look_at":
                    LookAt(target);
                    break;
            }
        }

        private void MoveTo(string targetId, bool run, string sitAfter)
        {
            var target = Resolve(targetId);
            var position = Vector3.zero;
            if (target != null)
            {
                position = target.position;
            }
            else if (targetId == "user" && Camera.main != null)
            {
                var camera = Camera.main;
                position = camera.transform.position + camera.transform.forward * 2.2f;
            }
            else
            {
                return;
            }

            position.y = baseY;
            if (sitting)
            {
                StandUp();
            }

            destination = position;
            speed = run ? runSpeed : walkSpeed;
            pendingSit = sitAfter;
            moving = true;
        }

        private Transform Resolve(string id)
        {
            if (string.IsNullOrEmpty(id) || id == "user")
            {
                return null;
            }
            return targets.TryGetValue(id, out var target) ? target : null;
        }

        private void Update()
        {
            if (!moving || avatar == null)
            {
                return;
            }

            avatar.position = Vector3.MoveTowards(
                avatar.position, destination, speed * Time.deltaTime);

            var direction = destination - avatar.position;
            direction.y = 0f;
            if (direction.sqrMagnitude > 0.001f)
            {
                avatar.rotation = Quaternion.Slerp(
                    avatar.rotation,
                    Quaternion.LookRotation(direction),
                    8f * Time.deltaTime);
            }

            if (Vector3.Distance(avatar.position, destination) <= 0.05f)
            {
                moving = false;
                if (!string.IsNullOrEmpty(pendingSit))
                {
                    Sit();
                    pendingSit = null;
                }
            }
        }

        private void Sit()
        {
            if (sitting)
            {
                return;
            }
            sitting = true;
            avatar.position += Vector3.down * 0.45f;
        }

        public void StandUp()
        {
            if (!sitting)
            {
                return;
            }
            sitting = false;
            avatar.position += Vector3.up * 0.45f;
        }

        private void LookAt(string targetId)
        {
            var target = Resolve(targetId);
            Vector3 point;
            if (target != null)
            {
                point = target.position;
            }
            else if (Camera.main != null)
            {
                point = Camera.main.transform.position;
            }
            else
            {
                return;
            }
            point.y = avatar.position.y;
            var direction = point - avatar.position;
            if (direction.sqrMagnitude > 0.001f)
            {
                avatar.rotation = Quaternion.LookRotation(direction);
            }
        }

        private IEnumerator JumpRoutine()
        {
            var start = avatar.position;
            for (var t = 0f; t < 0.45f; t += Time.deltaTime)
            {
                avatar.position = start + Vector3.up * Mathf.Sin(t / 0.45f * Mathf.PI) * 0.5f;
                yield return null;
            }
            avatar.position = start;
        }

        private IEnumerator WaveRoutine()
        {
            var start = avatar.rotation;
            for (var i = 0; i < 3; i++)
            {
                avatar.rotation = start * Quaternion.Euler(0f, 0f, 12f);
                yield return new WaitForSeconds(0.12f);
                avatar.rotation = start * Quaternion.Euler(0f, 0f, -12f);
                yield return new WaitForSeconds(0.12f);
            }
            avatar.rotation = start;
        }

        private IEnumerator NodRoutine()
        {
            var start = avatar.rotation;
            for (var i = 0; i < 2; i++)
            {
                avatar.rotation = start * Quaternion.Euler(18f, 0f, 0f);
                yield return new WaitForSeconds(0.14f);
                avatar.rotation = start;
                yield return new WaitForSeconds(0.14f);
            }
        }

        private IEnumerator ShakeRoutine()
        {
            var start = avatar.rotation;
            for (var i = 0; i < 3; i++)
            {
                avatar.rotation = start * Quaternion.Euler(0f, 14f, 0f);
                yield return new WaitForSeconds(0.1f);
                avatar.rotation = start * Quaternion.Euler(0f, -14f, 0f);
                yield return new WaitForSeconds(0.1f);
            }
            avatar.rotation = start;
        }
    }
}
