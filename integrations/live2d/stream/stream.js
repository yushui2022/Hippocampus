/* Stream shell: no input box, no dragging. The bridge pushes comments and turns. */
(() => {
  const BRIDGE = `ws://${location.hostname}:6220/ws`;
  // audio is opt-in: only the OBS browser source passes ?audio=1, so any other
  // tab previewing the stage stays silent instead of talking over it
  const MUTED = new URLSearchParams(location.search).get("audio") !== "1";
  const TTS_URL = "http://127.0.0.1:6211/tts";
  const ASSETS = "../assets/small/";
  const BG = "../assets/bg/bedroom.webm";

  // which clip is on screen; talking always returns to `resting`
  const CLIPS = { idle: "idle", talk: "talk", work: "work" };

  // several idle takes, chained so the loop is ~20 s instead of 5 s
  const IDLE_CLIPS = ["idle_a", "idle_b", "idle_c", "idle_d"];
  let idleIndex = 0;

  const video = document.getElementById("pet-video");
  const bgVideo = document.getElementById("bg-video");
  const subtitle = document.getElementById("subtitle");
  const subtitleText = document.getElementById("subtitle-text");
  const comments = document.getElementById("comments");
  const dialog = document.getElementById("dialog");
  const dialogLines = document.getElementById("dialog-lines");
  let dialogTimer = null;
  const DIALOG_KEEP = 3;
  const topic = document.getElementById("topic");

  let audioCtx = null;
  let socket = null;
  let retry = null;
  let talkWatchdog = null;
  let subtitleTimer = null;
  let currentSource = null;
  let resting = "idle";

  function playClip(name, loop) {
    if (video.dataset.clip === name && !video.ended) {
      return;
    }
    video.dataset.clip = name;
    video.loop = Boolean(loop);
    video.src = ASSETS + name + ".webm";
    video.play().catch(() => {});
  }

  function currentIdle() {
    return IDLE_CLIPS[idleIndex % IDLE_CLIPS.length];
  }

  function playRest() {
    if (resting === "work" && available.has("work")) {
      playClip("work", true);
      return;
    }
    playClip(currentIdle(), false);   // loop=false so `ended` fires and we rotate
  }

  video.addEventListener("ended", () => {
    const clip = video.dataset.clip || "";
    if (clip.startsWith("idle_")) {
      idleIndex += 1;
    }
    playRest();
  });

  const available = new Set(["idle", "talk"]);

  const TOPIC = { idle: "在线陪聊", work: "在摸鱼", talk: "说话中" };

  function setTopic(name) {
    topic.textContent = TOPIC[name] || "在线陪聊";
  }

  function setScene(name) {
    if (!CLIPS[name]) {
      return;
    }
    resting = available.has(name) ? name : "idle";
    playRest();
    setTopic(resting);
  }

  function probeClip(name) {
    // a missing work.webm would otherwise leave the stage blank
    return fetch(ASSETS + name + ".webm", { method: "HEAD" })
      .then((res) => {
        if (res.ok) {
          available.add(name);
        }
      })
      .catch(() => {});
  }

  function startBackground() {
    bgVideo.src = BG;
    bgVideo.play().catch(() => {
      bgVideo.removeAttribute("src");   // no clip yet: keep the still room
    });
  }

  function addComment(user, text) {
    const row = document.createElement("div");
    row.className = "comment";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = user || "观众";
    const body = document.createElement("span");
    body.textContent = text;
    row.append(who, body);
    comments.appendChild(row);
    while (comments.children.length > 6) {
      comments.removeChild(comments.firstChild);
    }
  }

  function pushDialog(text) {
    if (!text) {
      return;
    }
    const line = document.createElement("div");
    line.className = "line";
    line.textContent = text;
    dialogLines.appendChild(line);
    while (dialogLines.children.length > DIALOG_KEEP) {
      dialogLines.removeChild(dialogLines.firstChild);
    }
    Array.from(dialogLines.children).forEach((el, i, all) => {
      el.classList.toggle("old", i < all.length - 1);
    });
    dialog.classList.add("show");
    if (dialogTimer) {
      clearTimeout(dialogTimer);
    }
    dialogTimer = setTimeout(() => dialog.classList.remove("show"), 22000);
  }

  function showSubtitle(text, holdMs) {
    if (!text) {
      return;
    }
    subtitle.classList.remove("hidden");
    requestAnimationFrame(() => subtitle.classList.add("show"));
    subtitleText.textContent = text;
    if (subtitleTimer) {
      clearTimeout(subtitleTimer);
    }
    subtitleTimer = setTimeout(() => {
      subtitle.classList.remove("show");
      subtitleTimer = setTimeout(() => subtitle.classList.add("hidden"), 240);
    }, holdMs || 6000);
  }

  function ensureAudio() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  }

  async function speak(text) {
    if (!text) {
      return;
    }
    playClip("talk", true);
    setTopic("talk");
    if (MUTED) {
      setTimeout(() => {
        playRest();
        setTopic(resting);
      }, Math.min(9000, 1500 + text.length * 160));
      return;
    }
    try {
      const res = await fetch(TTS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, mood: "auto" }),
      });
      if (!res.ok) {
        throw new Error("tts " + res.status);
      }
      const buffer = await res.arrayBuffer();
      const ctx = ensureAudio();
      const audio = await ctx.decodeAudioData(buffer);
      const source = ctx.createBufferSource();
      source.buffer = audio;
      source.connect(ctx.destination);
      if (currentSource) {
        try { currentSource.stop(); } catch (e) { /* already stopped */ }
      }
      currentSource = source;
      const back = () => {
        if (talkWatchdog) {
          clearTimeout(talkWatchdog);
          talkWatchdog = null;
        }
        playRest();
        setTopic(resting);
      };
      source.onended = back;
      if (talkWatchdog) {
        clearTimeout(talkWatchdog);
      }
      talkWatchdog = setTimeout(back, ((audio.duration || 3) + 0.5) * 1000);
      source.start();
    } catch (error) {
      // no audio device or TTS down: keep the mouth moving for a beat, then idle
      setTimeout(() => playRest(), 1800 + text.length * 60);
    }
  }

  async function handleTurn(turn) {
    const lines = (turn.say || []).map((line) => line.text).filter(Boolean);
    if (!lines.length) {
      return;
    }
    const joined = lines.join("");
    pushDialog(joined);
    lines.forEach((line, index) => {
      setTimeout(() => showSubtitle(line, 5200), index * 900);
    });
    speak(joined);
  }

  function connect() {
    try {
      socket = new WebSocket(BRIDGE);
    } catch (error) {
      retry = setTimeout(connect, 2500);
      return;
    }

    socket.addEventListener("open", () => {
      topic.textContent = "在线陪聊";
      socket.send(JSON.stringify({ type: "hello", role: "stage" }));
    });

    socket.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (msg.type === "comment") {
        addComment(msg.user, msg.text);
      } else if (msg.type === "turn") {
        handleTurn(msg);
      } else if (msg.type === "scene") {
        setScene(msg.name);
      }
    });

    socket.addEventListener("close", () => {
      topic.textContent = "重连中…";
      retry = setTimeout(connect, 2500);
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  startBackground();
  Promise.all([probeClip("work"), probeClip("talk")]);
  playRest();
  setTopic("idle");
  connect();

  // keep the idle clip alive if the browser pauses it (tab switch etc.)
  setInterval(() => {
    if (video.paused) {
      video.play().catch(() => {});
    }
  }, 4000);
})();
