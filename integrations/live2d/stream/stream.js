/* Stream shell: no input box, no dragging. The bridge pushes comments and turns. */
(() => {
  const BRIDGE = `ws://${location.hostname}:6220/ws`;
  // audio is opt-in: only the OBS browser source passes ?audio=1, so any other
  // tab previewing the stage stays silent instead of talking over it
  const MUTED = new URLSearchParams(location.search).get("audio") !== "1";
  const TTS_URL = "http://127.0.0.1:6211/tts";
  const ASSETS = "../assets/small/";
  const BG = "../assets/bg/bedroom.webm";

  // 每个道具动作都是三段：掏出来 / 循环 / 收回去
  const SEQUENCES = {
    work: ["work_in", "work_loop", "work_out"],
    sit: ["sit_in", "sit_loop", "sit_out"],
    eat: ["eat_in", "eat_loop", "eat_out"],
    read: ["read_in", "read_loop", "read_out"],
  };

  // which clip is on screen; talking always returns to `resting`
  const CLIPS = { idle: "idle", talk: "talk", work: "work", sit: "sit", eat: "eat", read: "read" };

  // several idle takes, chained so the loop is ~20 s instead of 5 s
  const IDLE_CLIPS = ["idle_a", "idle_b", "idle_c", "idle_d"];

  // brain expression -> one-shot clip; played after the line is spoken
  const EXPR_CLIPS = {
    happy: "expr_happy",
    angry: "expr_angry",
    confused: "expr_confused",
    smirk: "expr_smirk",
    shy: "expr_shy",
    sad: "expr_sad",
    laugh: "expr_laugh",
    sigh: "expr_sigh",
    surprised: "expr_surprised",
  };

  // 一次性动作 /event 的名字 → 素材名
  const EVENT_CLIPS = {
    wave: "wave", nod: "nod", think: "think",
    heart: "expr_heart", clap: "expr_clap",
    laugh: "expr_laugh", sigh: "expr_sigh",
    surprised: "expr_surprised", sad: "expr_sad",
    shake_head: "shake_head", jump: "jump",
    stretch: "stretch", yawn: "yawn",
    touch_head: "touch_head", fix_hair: "fix_hair",
  };

  // 待机时的随机小动作，减少循环感
  const MICRO_CLIPS = ["stretch", "yawn", "touch_head", "fix_hair", "shake_head"];

  // 播完要接着补表情的一次性动作
  const ACTION_CLIPS = new Set([
    "nod", "wave", "think", "shake_head", "jump",
    "stretch", "yawn", "touch_head", "fix_hair",
    "expr_heart", "expr_clap",
  ]);

  let idleStreak = 0;

  // 弹幕关键词 → 动作，写在场控面板旁边的互动指南里
  const KEYWORDS = [
    [["比心", "爱心", "爱你"], "expr_heart"],
    [["贴贴", "害羞"], "expr_shy"],
    [["跳"], "jump"],
    [["摇头"], "shake_head"],
    [["摸头"], "touch_head"],
    [["理头发", "头发乱"], "fix_hair"],
    [["伸懒腰"], "stretch"],
    [["哈欠", "困了"], "yawn"],
    [["鼓掌", "拍手"], "expr_clap"],
    [["笑"], "expr_laugh"],
    [["叹气", "唉"], "expr_sigh"],
    [["惊讶", "真的假的", "不会吧"], "expr_surprised"],
    [["委屈", "难过"], "expr_sad"],
  ];

  // 礼物名字 → 动作
  const GIFT_CLIPS = [
    [["小心心", "爱心", "心动"], "expr_heart"],
    [["辣条", "打call", "牛哇"], "expr_laugh"],
    [["上舰", "舰长", "提督"], "expr_clap"],
    [["小花花", "花花", "玫瑰"], "expr_shy"],
  ];
  let lastGiftAt = 0;

  function pickClip(table, text) {
    const t = String(text || "");
    for (const [words, clip] of table) {
      if (words.some((w) => t.includes(w))) {
        return clip;
      }
    }
    return "";
  }
  let pendingExpr = "";
  let pendingAction = "";
  let idleIndex = 0;
  // 道具动作的三段状态
  let seqPhase = null;    // "in" | "loop" | "out"
  let seated = false;
  let wantStand = false;
  let pendingSit = "";
  let breakUntil = 0;
  let nextBreakAt = Date.now() + 90000;   // 两段休息之间至少隔一会儿

  const vids = [document.getElementById("pet-video"), document.getElementById("pet-video-b")];
  let front = 0;                 // which <video> is currently visible
  let currentClip = "";
  const bgVideo = document.getElementById("bg-video");
  const dustCanvas = document.getElementById("dust");
  const roomImg = document.getElementById("room");
  const subtitle = document.getElementById("subtitle");
  const subtitleText = document.getElementById("subtitle-text");
  const dialog = document.getElementById("dialog");
  const dialogLabel = document.getElementById("dialog-label");
  const dialogSite = document.getElementById("dialog-site");
  const dialogLines = document.getElementById("dialog-lines");
  let dialogTimer = null;
  const DIALOG_KEEP = 3;
  const topic = document.getElementById("topic");   // 可缺失（标签已去掉）
  const watching = document.getElementById("watching");
  const watchingText = document.getElementById("watching-text");
  const usageValue = document.getElementById("usage-value");
  const boardList = document.getElementById("board-list");
  const boardStats = document.getElementById("board-stats");
  let watchingTimer = null;
  let thinkTimer = null;
  let lastCommentAt = 0;

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
    if (resting === "idle") {
      playClip(currentIdle(), false);   // loop=false so `ended` fires and we rotate
      return;
    }
    const seq = SEQUENCES[resting];
    if (!seq) {
      resting = "idle";
      playClip(currentIdle(), false);
      return;
    }
    if (seqPhase === "in" && available.has(seq[0])) {
      playClip(seq[0], false);
      return;
    }
    if (!available.has(seq[1])) {        // 素材没齐就别切过去，免得黑屏
      resting = "idle";
      seqPhase = null;
      playClip(currentIdle(), false);
      return;
    }
    playClip(seq[1], true);
  }

  // 进入一套道具动作；skipIn 用于坐姿之间的直接切换
  function startSequence(name, skipIn) {
    const seq = SEQUENCES[name];
    if (!seq) {
      return;
    }
    pendingSit = "";
    resting = name;
    if (name === "eat" || name === "read") {
      breakUntil = Date.now() + 40000;      // 吃一会儿/看一会儿就收
    } else if (name === "sit") {
      breakUntil = Date.now() + 240000;
    }
    setTopic(name);
    if (!skipIn && available.has(seq[0])) {
      seqPhase = "in";
      playClip(seq[0], false);
      return;
    }
    seqPhase = "loop";
    playClip(seq[1], true);
  }

  // 把道具收回去，回到普通待机（坐着的还会站起来）
  function stopSequence() {
    const seq = SEQUENCES[resting];
    nextBreakAt = Date.now() + 240000;
    if (!seq) {
      resting = "idle";
      seqPhase = null;
      playRest();
      setTopic("idle");
      return;
    }
    if (available.has(seq[2])) {
      seqPhase = "out";
      playClip(seq[2], false);
      return;
    }
    seqPhase = null;
    seated = false;
    resting = "idle";
    playRest();
    setTopic("idle");
  }

  // 坐着/吃东西/看书时有人说话：先把手上的事收尾，回到站姿再开口
  function inBreak() {
    return resting === "sit" || resting === "eat" || resting === "read";
  }

  function ensureStanding(timeoutMs) {
    if (!inBreak()) {
      return Promise.resolve();
    }
    wantStand = true;
    stopSequence();
    return new Promise((resolve) => {
      const t0 = Date.now();
      const tick = () => {
        if (!inBreak() || Date.now() - t0 > (timeoutMs || 8000)) {
          wantStand = false;
          resolve();
          return;
        }
        setTimeout(tick, 120);
      };
      tick();
    });
  }

  function isTalking() {
    return currentClip === "talk" || ACTION_CLIPS.has(currentClip) ||
      currentClip.startsWith("expr_");
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
      if (resting === "idle") {
        idleStreak += 1;
        if (idleStreak >= 3) {
          const pool = MICRO_CLIPS.filter((n) => available.has(n));
          if (pool.length && Math.random() < 0.7) {
            idleStreak = 0;
            playClip(pool[Math.floor(Math.random() * pool.length)], false);
            return;
          }
        }
      }
    }
    const seq = SEQUENCES[resting];
    if (seq && clip === seq[0]) {          // 掏出来的动画放完，进入循环
      seqPhase = "loop";
      if (resting === "sit") {
        seated = true;
      }
      playRest();
      return;
    }
    if (seq && clip === seq[2]) {          // 收回去的动画放完
      seqPhase = null;
      if (wantStand && seated && (resting === "eat" || resting === "read")) {
        // 还坐着，吃完的东西也收好了，再补一个起身
        resting = "sit";
        seqPhase = "loop";
        stopSequence();
        return;
      }
      if (pendingSit && !wantStand) {
        const target = pendingSit;
        pendingSit = "";
        startSequence(target);
        return;
      }
      if (resting === "eat" || resting === "read") {
        if (seated && available.has("sit_loop")) {
          startSequence("sit", true);      // 吃完/看完，回到坐姿
        } else {
          resting = "idle";
          playRest();
          setTopic("idle");
        }
        return;
      }
      seated = false;
      wantStand = false;
      resting = "idle";
      playRest();
      setTopic("idle");
      return;
    }
    playRest();
    if (ACTION_CLIPS.has(clip)) {       // 动作之后补上这轮的表情
      playPendingExpr();
    }
  }));

  const available = new Set(["idle", "talk"]);

  const TOPIC = {
    idle: "在线陪聊", work: "在摸鱼", talk: "说话中",
    sit: "坐着歇会儿", eat: "在吃零食", read: "在看书",
  };

  function setTopic(name) {
    if (topic) {
      topic.textContent = TOPIC[name] || "在线陪聊";
    }
  }

  function setScene(name) {
    if (!CLIPS[name]) {
      return;
    }
    if (SEQUENCES[name]) {
      if (inBreak() && name === "work") {
        return;                            // 休息时自动上网别把她拽起来
      }
      if (resting === name && seqPhase === "loop") {
        return;                            // 已经在做这件事，别重播「掏出来」
      }
      startSequence(name);
      return;
    }
    // idle / talk：先把道具收回来
    if (SEQUENCES[resting]) {
      stopSequence();
      return;
    }
    resting = "idle";
    playRest();
    setTopic("idle");
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

  // 弹幕由直播平台自己叠在画面上，这里不再重复显示
  function addComment() {}

  // 顶部回复框常驻，开局先放一句占位，不至于是个空框
  function dialogPlaceholder() {
    dialog.classList.add("show");
    if (!dialogLines.children.length) {
      const line = document.createElement("div");
      line.className = "line old";
      line.id = "dialog-placeholder";
      line.textContent = "还没人说话…";
      dialogLines.appendChild(line);
    }
  }

  function showBrowsing(site, title, summary) {
    if (!dialogLines) return;
    dialog.classList.add("show");
    if (dialogLabel) dialogLabel.textContent = "上网中";
    if (dialogSite) dialogSite.textContent = site || "";
    dialogLines.textContent = "";
    if (title) {
      const t = document.createElement("div");
      t.className = "line title";
      t.textContent = title;
      dialogLines.appendChild(t);
    }
    if (summary) {
      const s2 = document.createElement("div");
      s2.className = "line summary";
      s2.textContent = summary;
      dialogLines.appendChild(s2);
    }
  }

  function pushDialog(text) {
    if (dialogLabel) dialogLabel.textContent = "她在说";
    if (dialogSite) dialogSite.textContent = "";
    if (!text) {
      return;
    }
    const ph = document.getElementById("dialog-placeholder");
    if (ph) ph.remove();
    // 从“上网实况”切回说话时，把标题和摘要清掉
    dialogLines.querySelectorAll(".title, .summary").forEach((el) => el.remove());
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
        if (pendingAction) {
          const clip = pendingAction;
          pendingAction = "";
          playClip(clip, false);
          return;
        }
        playPendingExpr();
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
        if (pendingAction) {
          const clip = pendingAction;
          pendingAction = "";
          playClip(clip, false);
          return;
        }
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

  // 今日 token 用量：走桥的 /stats，避免页面直连大脑（跨域）
  async function refreshUsage() {
    if (!usageValue) return;
    try {
      const res = await fetch(`http://${location.hostname}:6220/stats`, { cache: "no-store" });
      const d = await res.json();
      const t = d.tokens || {};
      const total = Number(t.total || (Number(t.prompt || 0) + Number(t.completion || 0)));
      usageValue.textContent = total > 0
        ? (total >= 10000 ? (total / 1000).toFixed(1) + "k" : String(total))
        : "0";
      renderStats(d.stats);
    } catch (e) {
      /* 桥没起来就先不显示 */
    }
  }

  function renderStats(stats) {
    if (!boardList || !stats) {
      return;
    }
    const users = Array.isArray(stats.users) ? stats.users : [];
    boardList.textContent = "";
    if (!users.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "还没有人上榜";
      boardList.appendChild(li);
    } else {
      users.slice(0, 5).forEach((u, i) => {
        const li = document.createElement("li");
        if (i === 0) {
          li.className = "top";
        }
        const rank = document.createElement("span");
        rank.className = "rank";
        rank.textContent = String(i + 1);
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = String(u.name || "观众");
        const count = document.createElement("span");
        count.className = "count";
        count.textContent = String(u.count || 0);
        li.append(rank, name, count);
        boardList.appendChild(li);
      });
    }
    if (boardStats) {
      boardStats.textContent =
        `弹幕 ${stats.comments || 0} · 互动 ${stats.people || 0} 人 · 礼物 ${stats.gifts || 0}`;
    }
  }

  function onGift(msg) {
    const user = String(msg.user || "观众");
    const gift = String(msg.gift || "礼物");
    const count = Number(msg.count || 1);
    showSubtitle(count > 1 ? `谢谢 ${user} 的 ${gift} ×${count}` : `谢谢 ${user} 的 ${gift}`, 5000);
    const clip = pickClip(GIFT_CLIPS, gift);
    if (clip && available.has(clip) && !inBreak() && Date.now() - lastGiftAt > 4000) {
      lastGiftAt = Date.now();
      playClip(clip, false);
    }
    refreshUsage();
  }

  function showWatching(site) {
    if (!site) return;
    watchingText.textContent = site;
    watching.classList.add("show");
    if (watchingTimer) clearTimeout(watchingTimer);
    watchingTimer = setTimeout(() => watching.classList.remove("show"), 24000);
  }

  async function handleTurn(turn) {
    const lines = (turn.say || []).map((line) => line.text).filter(Boolean);
    if (!lines.length) {
      return;
    }
    if (thinkTimer) {
      clearTimeout(thinkTimer);
      thinkTimer = null;
    }
    const joined = lines.join("");
    const site = String(turn.looking_at || "");
    pendingAction = "";
    for (const a of (turn.actions || [])) {
      const clip = EVENT_CLIPS[a && a.action];
      if (clip && available.has(clip)) {
        pendingAction = clip;
        break;
      }
    }

    // 自己上网时的自言自语：只留在对话框和顶部，不发声、不弹字幕
    if (turn.autonomous) {
      if (site) showWatching(site);
      pushDialog(joined);
      if (resting === "idle" && !seated) {
        startSequence("work", true);
      }
      return;
    }
    // 坐着的时候先站起来，站好了再开口
    await ensureStanding();
    if (site && !seated) {
      showWatching(site);
      // 先让人看见她“在看”，再开口
      playClip("work_loop", true);
      setTopic("work");
      await new Promise((r) => setTimeout(r, 1800));
    }
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
      setTopic("idle");
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
        lastCommentAt = Date.now();
        pendingSit = "";             // 有人来了就先别掏零食/书
        // 坐着的时候先起身，托腮放到站好之后
        const thinking = () => {
          if (!available.has("think")) {
            return;
          }
          playClip("think", true);
          setTopic("idle");
          // 回复迟迟不来时别一直托腮
          if (thinkTimer) clearTimeout(thinkTimer);
          thinkTimer = setTimeout(() => {
            thinkTimer = null;
            if (currentClip === "think") {
              playRest();
            }
          }, 25000);
        };
        const keyword = pickClip(KEYWORDS, msg.text);
        const react = () => {
          if (keyword && available.has(keyword)) {
            playClip(keyword, false);
          } else {
            thinking();
          }
        };
        if (inBreak()) {
          ensureStanding().then(react);
        } else {
          react();
        }
      } else if (msg.type === "turn") {
        handleTurn(msg);
      } else if (msg.type === "action") {
        const clip = EVENT_CLIPS[String(msg.name || "")];
        if (clip && available.has(clip) && !inBreak()) {
          playClip(clip, false);
        }
      } else if (msg.type === "browsing") {
        showBrowsing(msg.site, msg.title, msg.summary);
        // 正在说话/思考/休息时不要被自动上网打断
        if (!isTalking() && !inBreak() && Date.now() - lastCommentAt > 12000) {
          setScene("work");
        }
      } else if (msg.type === "gift") {
        onGift(msg);
      } else if (msg.type === "scene") {
        setScene(msg.name);
      }
    });

    socket.addEventListener("close", () => {
      if (topic) topic.textContent = "重连中…";
      retry = setTimeout(connect, 2500);
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  startBackground();
  refreshUsage();
  setInterval(refreshUsage, 30000);
  dialogPlaceholder();
  startDust();
  applyTimeOfDay();
  // 默认就在刷手机，不是傻站着：等可用性探测完再进
  Promise.all([
    probeClip("work_in"), probeClip("work_loop"), probeClip("work_out"), probeClip("talk"),
    probeClip("expr_happy"), probeClip("expr_angry"),
    probeClip("expr_confused"), probeClip("expr_smirk"), probeClip("expr_shy"),
    probeClip("expr_sad"), probeClip("expr_laugh"), probeClip("expr_sigh"),
    probeClip("expr_surprised"), probeClip("expr_heart"), probeClip("expr_clap"),
    probeClip("shake_head"), probeClip("jump"), probeClip("stretch"),
    probeClip("yawn"), probeClip("touch_head"), probeClip("fix_hair"),
    probeClip("think"), probeClip("nod"), probeClip("wave"),
    probeClip("sit_in"), probeClip("sit_loop"), probeClip("sit_out"),
    probeClip("eat_in"), probeClip("eat_loop"), probeClip("eat_out"),
    probeClip("read_in"), probeClip("read_loop"), probeClip("read_out"),
  ]).then(() => {
    startSequence("work");
  });
  connect();

  // 安静太久就自己找点事做：坐下 → 掏零食或书 → 起身
  const SIT_AFTER = 100000;       // 没人说话 100 秒后自己找点事
  const ACTIVITY_AFTER = 45000;   // 坐着 45 秒后掏点东西
  function pickBreak() {
    if (available.has("sit_in")) {
      return "sit";
    }
    // 没有坐下的素材就站着掏零食/书
    const pick = Math.random() < 0.5 ? "eat" : "read";
    if (available.has(pick + "_in") && available.has(pick + "_loop")) {
      return pick;
    }
    return "";
  }

  setInterval(() => {
    const quiet = Date.now() - lastCommentAt;
    if (inBreak()) {
      if (seqPhase === "loop" && Date.now() > breakUntil && !isTalking()) {
        stopSequence();
        return;
      }
      if (seated && resting === "sit" && seqPhase === "loop" && quiet > ACTIVITY_AFTER) {
        const pick = Math.random() < 0.5 ? "eat" : "read";
        if (available.has(pick + "_in") && available.has(pick + "_loop")) {
          startSequence(pick);
          return;
        }
      }
      return;
    }
    if (quiet > SIT_AFTER && Date.now() > nextBreakAt && !isTalking() &&
        (resting === "idle" || resting === "work")) {
      const target = pickBreak();
      if (!target) {
        return;
      }
      if (SEQUENCES[resting]) {
        pendingSit = target;       // 先把手机收好，再换下一个动作
        stopSequence();
        return;
      }
      startSequence(target);
    }
  }, 10000);

  // keep the idle clip alive if the browser pauses it (tab switch etc.)
  setInterval(() => {
    const v = frontVideo();
    if (v.paused) {
      v.play().catch(() => {});
    }
  }, 4000);

  // 调试用：可以从控制台手动触发一轮对话或某个动作
  window.__stage = {
    turn: handleTurn,
    speak,
    start: startSequence,
    stop: stopSequence,
    state: () => ({ resting, seqPhase, seated, currentClip, front }),
  };
})();
