const WS_URL = "ws://127.0.0.1:6200/ws";
const TTS_URL = "http://127.0.0.1:6211/tts";

const room = document.getElementById("room");
const character = document.getElementById("character");
const headGroup = document.getElementById("head-group");
const exprLayer = document.getElementById("expr");
const blinkLayer = document.getElementById("blink");
const talkLayer = document.getElementById("talk");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const formToggle = document.getElementById("form-toggle");

let socket = null;
let retryTimer = null;
let blinkTimer = null;
let talkTimer = null;

let audioContext = null;
let analyser = null;
let mouthRunning = false;

/* -- motion state ------------------------------------------------------ */
let targetX = 0;
let targetY = 0;
let currentX = 0;
let currentY = 0;
let lastFrame = performance.now();

let posX = 0;
let targetPosX = 0;
let walkSpeed = 200;
let walking = false;
let walkPhase = 0;

let weight = 0;
let weightTarget = 0;
let nextIdle = 0;

let jumpY = 0;
let jumpVelocity = 0;

let sit = 0;
let sitTarget = 0;

let headTilt = 0;
let headTiltTarget = 0;
let headDX = 0;
let headDXTarget = 0;
let headDY = 0;
let nextTilt = 0;
let nextGlance = 0;
let bounce = 0;
let nodUntil = 0;
let shakeUntil = 0;

let dragging = false;
let dragMoved = false;
let dragStartX = 0;
let dragStartPos = 0;

const ANCHORS = {
  living: 0,
  sofa: -170,
  chair_gift: -300,
  plant: 170,
  balcony: 250,
  user: 0
};

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

/* -- pointer / drag / walk -------------------------------------------- */
window.addEventListener("pointermove", (event) => {
  targetX = (event.clientX / window.innerWidth - 0.5) * 18;
  targetY = (event.clientY / window.innerHeight - 0.5) * 10;

  if (dragging) {
    const delta = event.clientX - dragStartX;
    if (Math.abs(delta) > 5) {
      dragMoved = true;
    }
    posX = clamp(dragStartPos + delta, -320, 320);
    targetPosX = posX;
  }
});

character.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  dragging = true;
  dragMoved = false;
  dragStartX = event.clientX;
  dragStartPos = posX;
  character.setPointerCapture?.(event.pointerId);
});

window.addEventListener("pointerup", () => {
  if (!dragging) {
    return;
  }
  dragging = false;
  if (!dragMoved) {
    bounce = 1;
    blinkOnce();
    setExpression("happy", 1800);
  }
});

document.getElementById("stage").addEventListener("pointerdown", (event) => {
  if (event.target.closest("#character, #chat, #composer, #topbar")) {
    return;
  }
  const home = window.innerWidth * 0.7;
  targetPosX = clamp(event.clientX - home, -320, 320);
  walkSpeed = 210;
});

/* -- actions from the brain -------------------------------------------- */
function nod() {
  nodUntil = performance.now() + 700;
}

function shakeHead() {
  shakeUntil = performance.now() + 700;
}

function jump() {
  jumpVelocity = -420;
}

function lookAt(target) {
  if (target === "user") {
    headDXTarget = clamp(currentX * 0.5, -8, 8);
    return;
  }
  const anchor = ANCHORS[target];
  if (anchor !== undefined) {
    headDXTarget = Math.sign(anchor || 1) * 6;
  }
}

function walkTo(target, run) {
  if (target === "user") {
    return;
  }
  const anchor = ANCHORS[target];
  if (anchor === undefined) {
    return;
  }
  targetPosX = anchor;
  walkSpeed = run ? 380 : 210;
}

function wave() {
  setExpression("happy", 1400);
  weightTarget = 8;
  window.setTimeout(() => {
    weightTarget = 0;
  }, 650);
}

function runActions(actions) {
  (actions || []).forEach((action) => {
    const verb = action.action || action.do;
    switch (verb) {
      case "nod":
        nod();
        break;
      case "shake_head":
        shakeHead();
        break;
      case "jump":
        jump();
        break;
      case "wave":
        wave();
        break;
      case "look_at":
      case "turn_to":
        lookAt(action.target);
        break;
      case "walk_to":
        walkTo(action.target, false);
        break;
      case "run_to":
        walkTo(action.target, true);
        break;
      case "sit":
        walkTo(action.target, false);
        sitTarget = 1;
        break;
      case "stand_up":
        sitTarget = 0;
        break;
    }
  });
}

/* -- idle frame -------------------------------------------------------- */
function frame(now) {
  const dt = Math.min(0.05, (now - lastFrame) / 1000);
  lastFrame = now;
  const t = now / 1000;

  currentX += (targetX - currentX) * 0.04;
  currentY += (targetY - currentY) * 0.04;

  const dx = targetPosX - posX;
  if (Math.abs(dx) > 1.5) {
    const step = Math.min(Math.abs(dx), walkSpeed * dt);
    posX += Math.sign(dx) * step;
    walking = true;
    walkPhase += dt * 10;
  } else {
    posX = targetPosX;
    walking = false;
  }

  if (!dragging && !walking && now > nextIdle) {
    weightTarget = (Math.random() - 0.5) * 9;
    nextIdle = now + 8000 + Math.random() * 7000;
    window.setTimeout(() => {
      weightTarget = 0;
    }, 3200);
  }
  weight += (weightTarget - weight) * 0.035;

  if (jumpVelocity !== 0 || jumpY !== 0) {
    jumpVelocity += 1500 * dt;
    jumpY += jumpVelocity * dt;
    if (jumpY >= 0) {
      jumpY = 0;
      jumpVelocity = 0;
    }
  }

  sit += (sitTarget - sit) * 0.08;

  if (now > nextTilt) {
    headTiltTarget = (Math.random() - 0.5) * 2.6;
    nextTilt = now + 5200 + Math.random() * 7000;
  }
  if (now > nextGlance) {
    headDXTarget = (Math.random() - 0.5) * 7;
    nextGlance = now + 3600 + Math.random() * 5200;
  }

  headTilt += (headTiltTarget - headTilt) * 0.05;
  headDX += (headDXTarget + currentX * 0.32 - headDX) * 0.06;
  headDY += (currentY * 0.22 - headDY) * 0.06;
  bounce *= 0.88;

  const breathe = Math.sin(t * 1.45) * 0.0045;
  const sway = Math.sin(t * 0.36) * 0.42 + currentX * 0.01;
  const walkBob = walking ? Math.abs(Math.sin(walkPhase)) * 5 : 0;
  const lean = walking ? Math.sign(dx) * 0.9 : 0;
  const nodOffset = nodUntil > now ? Math.sin((nodUntil - now) / 80) * 3 : 0;
  const shakeOffset = shakeUntil > now ? Math.sin((shakeUntil - now) / 50) * 4.5 : 0;
  const sitOffset = sit * 18;

  character.style.transform =
    `translateX(calc(-50% + ${posX + weight + currentX * 0.25}px)) ` +
    `translateY(${currentY * 0.14 + walkBob + sitOffset + jumpY - bounce * 7}px) ` +
    `rotate(${sway + lean}deg) scaleY(${1 + breathe - sit * 0.02})`;

  headGroup.style.transform =
    `translate(${headDX}px, ${headDY + nodOffset}px) rotate(${headTilt + shakeOffset}deg)`;

  room.style.transform =
    `scale(1.03) translate(${-currentX * 0.32 - posX * 0.08}px, ${-currentY * 0.2}px)`;

  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* -- expressions ------------------------------------------------------- */
let expressionTimer = null;
let currentExpression = "";
let expressionEnabled = false;

function setExpression(name, holdMs = 5000) {
  if (!expressionEnabled) {
    return;
  }
  const key = (name || "").toLowerCase();
  const allowed = ["happy", "sad", "angry", "shy"];
  if (expressionTimer) {
    clearTimeout(expressionTimer);
    expressionTimer = null;
  }
  if (!allowed.includes(key)) {
    exprLayer.style.opacity = "0";
    currentExpression = "";
    return;
  }
  if (currentExpression !== key) {
    exprLayer.src = "../assets/expr_" + key + ".png";
    currentExpression = key;
  }
  exprLayer.style.opacity = "1";
  expressionTimer = window.setTimeout(() => {
    exprLayer.style.opacity = "0";
    currentExpression = "";
  }, holdMs);
}

/* -- blink ------------------------------------------------------------- */
function blinkOnce() {
  blinkLayer.style.opacity = "1";
  window.setTimeout(() => {
    blinkLayer.style.opacity = "0";
  }, 120);
}

function scheduleBlink() {
  const wait = 3000 + Math.random() * 3600;
  blinkTimer = window.setTimeout(() => {
    blinkOnce();
    scheduleBlink();
  }, wait);
}
scheduleBlink();

/* -- audio ------------------------------------------------------------- */
function ensureAudio() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === "suspended") {
    audioContext.resume();
  }
  return audioContext;
}

function driveMouthFromAnalyser() {
  if (!analyser) {
    return;
  }
  mouthRunning = true;
  const data = new Uint8Array(analyser.fftSize);
  const tick = () => {
    if (!mouthRunning) {
      return;
    }
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / data.length);
    talkLayer.style.opacity = rms > 0.02 ? "1" : "0";
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

async function speak(text) {
  if (!text) {
    return;
  }
  try {
    const response = await fetch(TTS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, mood: "auto" })
    });
    if (!response.ok) {
      throw new Error("tts " + response.status);
    }
    const buffer = await response.arrayBuffer();
    const context = ensureAudio();
    const audio = await context.decodeAudioData(buffer);
    const source = context.createBufferSource();
    source.buffer = audio;

    analyser = analyser || context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    analyser.connect(context.destination);

    source.onended = () => {
      mouthRunning = false;
      talkLayer.style.opacity = "0";
    };
    source.start();
    driveMouthFromAnalyser();
  } catch (error) {
    startTalking(900 + text.length * 70);
  }
}

/* -- talk fallback ----------------------------------------------------- */
function startTalking(durationMs) {
  if (talkTimer) {
    clearInterval(talkTimer);
    talkTimer = null;
  }
  const until = performance.now() + durationMs;
  talkTimer = window.setInterval(() => {
    const open = Math.random() > 0.35;
    talkLayer.style.opacity = open ? "1" : "0";
    if (performance.now() > until) {
      talkLayer.style.opacity = "0";
      clearInterval(talkTimer);
      talkTimer = null;
    }
  }, 90);
}

/* -- chat -------------------------------------------------------------- */
function addBubble(text, kind = "bot") {
  const el = document.createElement("div");
  el.className = "bubble " + kind;
  el.textContent = text;
  logEl.appendChild(el);
  while (logEl.children.length > 18) {
    logEl.removeChild(logEl.firstChild);
  }
  logEl.scrollTop = logEl.scrollHeight;
  return el;
}

function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.className = "status " + (ok ? "ok" : "bad");
}

function connect() {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  setStatus("连接中…", false);

  try {
    socket = new WebSocket(WS_URL);
  } catch (error) {
    setStatus("连不上大脑服务", false);
    retryTimer = setTimeout(connect, 3000);
    return;
  }

  socket.addEventListener("open", () => {
    setStatus("已连接", true);
    socket.send(JSON.stringify({
      type: "hello",
      user_id: "web",
      user_name: "你",
      scene: {
        places: [
          { id: "living", name: "客厅" },
          { id: "balcony", name: "阳台" }
        ],
        objects: [
          { id: "sofa", name: "沙发", place: "living", affords: ["sit"] },
          { id: "chair_gift", name: "你送的椅子", place: "living", affords: ["sit"] },
          { id: "plant", name: "绿萝", place: "balcony", affords: ["look_at"] }
        ],
        actions: [
          "walk_to", "run_to", "sit", "stand_up", "jump",
          "turn_to", "look_at", "wave", "nod", "shake_head"
        ]
      }
    }));
  });

  socket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (error) {
      return;
    }

    if (message.type === "welcome") {
      setStatus("在呢", true);
      if (!window.__greeted) {
        window.__greeted = true;
        addBubble("在呢。", "bot");
      }
      const params = new URLSearchParams(window.location.search);
      const autoSay = params.get("say");
      if (autoSay) {
        window.setTimeout(() => {
          input.value = autoSay;
          form.requestSubmit();
        }, 600);
      }
      return;
    }

    if (message.type === "turn") {
      const bubbles = message.say || [];
      bubbles.forEach((line, index) => {
        window.setTimeout(() => {
          addBubble(line.text, "bot");
        }, index * 520);
      });
      const spoken = bubbles.map((line) => line.text).join("");
      setExpression(message.expression, 5200);
      runActions(message.actions);
      speak(spoken);
      return;
    }

    if (message.type === "error") {
      addBubble("服务端错误：" + (message.message || ""), "system");
    }
  });

  socket.addEventListener("close", () => {
    setStatus("断线，重连中…", false);
    retryTimer = setTimeout(connect, 3000);
  });

  socket.addEventListener("error", () => {
    setStatus("连接失败", false);
  });
}

/* -- form switch ------------------------------------------------------- */
let currentForm = localStorage.getItem("taozi-form") || "big";

async function loadForm(name) {
  const base = "../assets/" + name + "/";
  const manifest = await (await fetch(base + "manifest.json")).json();
  document.getElementById("body").src = base + "body.png";
  document.getElementById("head").src = base + "head.png";
  document.getElementById("neck").src = base + "neck_fill.png";
  blinkLayer.src = base + "blink_head.png";
  talkLayer.src = base + "talk_mouth.png";
  exprLayer.src = "../assets/blank.png";
  exprLayer.style.opacity = "0";
  talkLayer.style.opacity = "0";
  blinkLayer.style.opacity = "0";
  mouthRunning = false;
  expressionEnabled = false;
  currentExpression = "";

  character.style.setProperty("--char-height", manifest.heightVh + "vh");
  character.style.setProperty("--char-aspect", manifest.aspect);
  character.style.setProperty("--pivot-x", (manifest.pivot[0] * 100).toFixed(2) + "%");
  character.style.setProperty("--pivot-y", (manifest.pivot[1] * 100).toFixed(2) + "%");

  character.style.left = name === "big" ? "72%" : "70%";
  currentForm = name;
  localStorage.setItem("taozi-form", name);
  formToggle.textContent = name === "big" ? "变小鲸" : "变大鲸";
}

formToggle.addEventListener("click", () => {
  loadForm(currentForm === "big" ? "small" : "big").catch(() => {});
});

loadForm(currentForm).catch(() => {});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) {
    return;
  }
  input.value = "";
  addBubble(text, "user");

  if (!socket || socket.readyState !== WebSocket.OPEN) {
    addBubble("还没连上她，等一下。", "system");
    return;
  }
  socket.send(JSON.stringify({ type: "user_text", text: text, place: "living" }));
});

connect();
