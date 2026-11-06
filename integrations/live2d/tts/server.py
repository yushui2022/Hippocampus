#!/usr/bin/env python3
"""Resident TTS service for Taozijie.

Loads Qwen3-TTS once, then serves WAV over HTTP. No API keys, no cloud.

    POST /tts   {"text": "...", "mood": "warm"|"soft"|"auto"}
    GET  /health
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import wave
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model

MODEL_ID = os.environ.get("TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit")
VOICE = os.environ.get("TTS_VOICE", "sohee")

INSTRUCT = {
    # 直播默认：雌小鬼，轻挑版（2026-11-04 主人选定）
    "brat": "轻松地挑衅，像在开玩笑，句尾微微上扬，带一点笑意，不要凶，不要温柔，不要播音腔。",
    "soft": "声音轻一点、软一点，正常语速，像朋友聊天，不要拖长音，不要播音腔。",
    "warm": "像朋友聊天一样，语气温柔，语速稍慢，不要播音腔，带一点笑意。",
    "lively": "轻松、俏皮一点，像在跟熟人说话，不要朗读感。",
}

MODEL = None
LOCK = threading.Lock()


def pick_mood(requested: str) -> str:
    """Explicit mood wins; otherwise use the configured default."""
    if requested in INSTRUCT:
        return requested
    return os.environ.get("DEFAULT_MOOD", "brat")


def synthesize(text: str, mood: str) -> bytes:
    instruct = INSTRUCT[pick_mood(mood)]
    with LOCK:
        directory = tempfile.mkdtemp(prefix="taozi-tts-")
        generate_audio(
            text=text,
            model=MODEL,
            voice=VOICE,
            instruct=instruct,
            lang_code="zh",
            output_path=directory,
            file_prefix="tts",
            audio_format="wav",
            speed=1.0,
            verbose=False,
        )
        files = sorted(Path(directory).glob("*.wav"))
        if not files:
            raise RuntimeError("tts produced no audio")
        return _pad_short(files[0].read_bytes(), 0.7)


def _pad_short(data: bytes, min_seconds: float) -> bytes:
    """QQ drops very short voice clips; pad to a natural minimum length."""
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            params = reader.getparams()
            frames = reader.readframes(reader.getnframes())
        seconds = len(frames) / float(params.framerate * params.sampwidth * params.nchannels)
        if seconds >= min_seconds:
            return data
        missing = int((min_seconds - seconds) * params.framerate)
        frames += b"\x00" * (missing * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as writer:
            writer.setparams(params)
            writer.writeframes(frames)
        return out.getvalue()
    except Exception:
        return data


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/health"):
            body = json.dumps(
                {
                    "ok": True,
                    "model": MODEL_ID,
                    "voice": VOICE,
                    "moods": list(INSTRUCT),
                    "loaded": MODEL is not None,
                }
            ).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/tts"):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}

        text = str(payload.get("text") or "").strip()
        if not text:
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        mood = str(payload.get("mood") or "auto")
        started = time.time()
        try:
            audio = synthesize(text[:300], mood)
        except Exception as exc:  # noqa: BLE001
            error = json.dumps({"error": str(exc)}).encode()
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error)))
            self.end_headers()
            self.wfile.write(error)
            return

        print(
            "[tts %.2fs] %s -> %d bytes"
            % (time.time() - started, mood, len(audio)),
            flush=True,
        )
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)


def main() -> None:
    global MODEL
    print("loading", MODEL_ID, flush=True)
    MODEL = load_model(MODEL_ID)
    host = os.environ.get("TTS_HOST", "0.0.0.0")
    print("ready on {}:6211".format(host), flush=True)
    ThreadingHTTPServer((host, 6211), Handler).serve_forever()


if __name__ == "__main__":
    main()
