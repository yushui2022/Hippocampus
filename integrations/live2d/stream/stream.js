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

  // brain expression -> one-shot clip; played after the line is spoken
  const EXPR_CLIPS = {
    happy: "expr_happy",
    angry: "expr_angry",
    confused: "expr_confused",
    smirk: "expr_smirk",
  };
  let pendingExpr = "";
  let idleIndex = 0;
  // work scene is a three-stage sequence: pull the tablet out, loop, put it away
  let workPhase = null;   // "in" | "loop" | "out"

  const vids = [document.getElementById("pet-video"), document.getElementById("pet-video-b")];
  let front = 0;                 // which <video> is currently visible
  let currentClip = "";
  const bgVideo = document.getElementById("bg-video");
  const dustCanvas = document.getElementById("dust");
  const roomImg = document.getElementById("room");
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

  // Two stacked <video> elements: the new clip fades in on the hidden one while
  // the old keeps playing underneath, so a decoder restart or a pose difference
  // between takes is never visible as a jump.
  function playClip(name, loop) {
    if (currentClip === name) {
      return;
    }
    const back = 1 - front;
    const next = vids[back];
    const prev = vids[front];
    let done = false;

    const reveal = () => {
      if (done) return;
      done = true;
      next.loop = Boolean(loop);
      next.dataset.clip = name;
      next.play().catch(() => {});
      next.style.opacity = "1";
      prev.style.opacity = "0";
      front = back;
      currentClip = name;
    };

    next.loop = Boolean(loop);
    next.src = ASSETS + name + ".webm";
    next.dataset.clip = name;
    if (next.readyState >= 2) {
      reveal();
    } else {
      next.addEventListener("canplay", reveal, { once: true });
      // never stall the stage if the decode is slow
      setTimeout(reveal, 220);
      next.play().catch(() => {});
    }
  }

  function frontVideo() {
    return vids[front];
  }

  function playPendingExpr() {
    if (!pendingExpr) {
      return;
    }
    const clip = EXPR_CLIPS[pendingExpr];
    pendingExpr = "";
    if (clip && available.has(clip)) {
      playClip(clip, false);
    }
  }

  function currentIdle() {
    return IDLE_CLIPS[idleIndex % IDLE_CLIPS.length];
  }

  function playRest() {
    if (resting === "work" && workPhase === "loop" && available.has("work_loop")) {
      playClip("work_loop", true);
      return;
    }
    if (resting === "work" && available.has("work_loop")) {
      workPhase = "loop";
      playClip("work_loop", true);
      return;
    }
    playClip(currentIdle(), false);   // loop=false so `ended` fires and we rotate
  }

  function startWork() {
    resting = "work";
    setTopic("work");
    if (available.has("work_in") && available.has("work_loop")) {
      workPhase = "in";
      playClip("work_in", false);
    } else {
      workPhase = "loop";
      playRest();
    }
  }

  function stopWork() {
    if (available.has("work_out") && currentClip !== "work_out") {
      workPhase = "out";
      playClip("work_out", false);
      return;
    }
    workPhase = null;
    resting = "idle";
    playRest();
    setTopic("idle");
  }

  vids.forEach((v) => v.addEventListener("ended", () => {
    if (v !== frontVideo()) {
      return;
    }
    const clip = v.dataset.clip || "";
    if (clip.startsWith("expr_") && pendingExpr) {
      pendingExpr = "";
    }
    if (clip.startsWith("idle_")) {
      idleIndex += 1;
    }
    if (clip === "work_in") {
      workPhase = "loop";
      playRest();
      return;
    }
    if (clip === "work_out") {
      workPhase = null;
      resting = "idle";
      playRest();
      setTopic("idle");
      return;
    }
    playRest();
  }));

  const available = new Set(["idle", "talk"]);

  const TOPIC = { idle: "在线陪聊", work: "在摸鱼", talk: "说话中" };

  function setTopic(name) {
    topic.textContent = TOPIC[name] || "在线陪聊";
  }

  function setScene(name) {
    if (!CLIPS[name]) {
      return;
    }
    if (name === "work") {
      startWork();
      return;
    }
    if (resting === "work") {
      stopWork();
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

  // 浮尘：慢慢往上飘的光点，让静态背景有空气感
  function startDust() {
    if (!dustCanvas) return;
    const ctx = dustCanvas.getContext("2d");
    let w = 0, h = 0, dots = [];

    function resize() {
      const r = dustCanvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      w = Math.max(1, Math.round(r.width));
      h = Math.max(1, Math.round(r.height));
      dustCanvas.width = w * dpr;
      dustCanvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      dots = Array.from({ length: 26 }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 0.6 + Math.random() * 1.8,
        vy: 3 + Math.random() * 9,
        vx: (Math.random() - 0.5) * 4,
        a: 0.10 + Math.random() * 0.28,
      }));
    }

    let last = performance.now();
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, w, h);
      for (const d of dots) {
        d.y -= d.vy * dt;
        d.x += d.vx * dt;
        if (d.y < -6) { d.y = h + 6; d.x = Math.random() * w; }
        if (d.x < -6) d.x = w + 6;
        if (d.x > w + 6) d.x = -6;
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 246, 224, ${d.a})`;
        ctx.fill();
      }
      requestAnimationFrame(frame);
    }

    resize();
    window.addEventListener("resize", resize);
    requestAnimationFrame(frame);
  }

  // 白天 7:00-18:59，其余时间用夜晚配色
  function applyTimeOfDay() {
    const hour = new Date().getHours();
    const night = hour >= 19 || hour < 7;
    document.body.classList.toggle("night", night);
    const name = night ? "../assets/room_night.png" : "../assets/room_day.png";
    fetch(name, { method: "HEAD" })
      .then((res) => { if (res.ok) roomImg.src = name; })
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
        playPendingExpr();
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
    pendingExpr = String(turn.expression || "").toLowerCase();
    if (!EXPR_CLIPS[pendingExpr]) {
      pendingExpr = "";
    }
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
  startDust();
  applyTimeOfDay();
  Promise.all([
    probeClip("work_in"), probeClip("work_loop"), probeClip("work_out"), probeClip("talk"),
    probeClip("expr_happy"), probeClip("expr_angry"),
    probeClip("expr_confused"), probeClip("expr_smirk"),
  ]);
  playRest();
  setTopic("idle");
  connect();

  // keep the idle clip alive if the browser pauses it (tab switch etc.)
  setInterval(() => {
    const v = frontVideo();
    if (v.paused) {
      v.play().catch(() => {});
    }
  }, 4000);
})();
