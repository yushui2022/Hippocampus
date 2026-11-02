"""LLM-backed memory summarizer.

The prompt alone does nothing: this module sends the recent turns to a chat
model, validates the JSON it returns, and falls back to the extractive path on
any failure. The model can be DeepSeek, any OpenAI-compatible endpoint, or a
local Ollama chat model.

    summarizer = LLMSummarizer.from_env()
    hippo = Hippocampus(..., summarizer=summarizer, async_summary=True)
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .models import Event, Summary

_ALLOWED_SENTIMENT = {"positive", "neutral", "negative"}
_ALLOWED_EMOTION = {"开心", "难过", "焦虑", "愤怒", "孤独", "疲惫", "平静", ""}
_PROMPT_PATH = Path(__file__).with_name("prompts") / "summarize.txt"


class SummaryError(RuntimeError):
    pass


def load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "把以下对话压缩成第一人称记忆。只使用对话中出现的信息。"
            "输出 JSON：gist / key_facts / topics / sentiment / importance / emotion。\n"
        )


def format_dialogue(events: Iterable[Event], max_item_chars: int = 500) -> str:
    lines: List[str] = []
    for event in sorted(events, key=lambda e: e.resolved_at()):
        text = (event.text or "").strip()
        if not text:
            continue
        if len(text) > max_item_chars:
            text = text[:max_item_chars] + "…"
        stamp = event.resolved_at().strftime("%Y-%m-%d %H:%M")
        if event.actor == "assistant":
            who = "我"
        else:
            who = str(
                event.meta.get("sender_name")
                or event.meta.get("nickname")
                or "你"
            )
        lines.append("[{}] {}: {}".format(stamp, who, text))
    return "\n".join(lines)


def parse_summary(raw: str) -> Summary:
    """Parse a model response defensively; raise SummaryError if unusable."""
    text = (raw or "").strip()
    if not text:
        raise SummaryError("empty response")

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SummaryError("invalid JSON: {}".format(exc))

    if not isinstance(data, dict):
        raise SummaryError("response is not a JSON object")

    gist = str(data.get("gist") or "").strip()
    if not gist:
        raise SummaryError("missing gist")
    gist = gist[:240]

    def string_list(value: Any, limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        out = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text[:60])
            if len(out) >= limit:
                break
        return out

    sentiment = str(data.get("sentiment") or "neutral").strip().lower()
    if sentiment not in _ALLOWED_SENTIMENT:
        sentiment = "neutral"

    emotion = str(data.get("emotion") or "").strip()
    if emotion not in _ALLOWED_EMOTION:
        emotion = ""

    try:
        importance = float(data.get("importance", 0.5))
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    return Summary(
        gist=gist,
        key_facts=string_list(data.get("key_facts"), 8),
        topics=string_list(data.get("topics"), 6),
        sentiment=sentiment,
        importance=importance,
        emotion=emotion,
    )


class LLMSummarizer:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        *,
        timeout: float = 60.0,
        retries: int = 2,
        temperature: float = 0.2,
        max_tokens: int = 700,
        max_input_chars: int = 12000,
        prompt: Optional[str] = None,
        transport: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_input_chars = max_input_chars
        self.prompt = prompt or load_prompt()
        self._transport = transport

    @classmethod
    def from_env(cls) -> "LLMSummarizer":
        key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("HIPPO_SUMMARY_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not key:
            raise SummaryError("DEEPSEEK_API_KEY / HIPPO_SUMMARY_KEY is not set")
        return cls(
            api_key=key,
            base_url=os.environ.get(
                "HIPPO_SUMMARY_BASE", "https://api.deepseek.com/v1"
            ),
            model=os.environ.get("HIPPO_SUMMARY_MODEL", "deepseek-chat"),
        )

    def summarize(
        self, events: Iterable[Event], *, persona: Optional[str] = None
    ) -> Summary:
        dialogue = format_dialogue(events)
        if len(dialogue) > self.max_input_chars:
            dialogue = dialogue[-self.max_input_chars :]
        if not dialogue.strip():
            raise SummaryError("empty dialogue")

        system = self.prompt.strip()
        if persona and persona.strip():
            system += "\n\n角色设定（只用于语气，不要据此补充事实）：\n" + persona.strip()[:2000]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": dialogue},
        ]

        last_error: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                raw = self._call(messages)
                return parse_summary(raw)
            except Exception as exc:  # noqa: BLE001 - provider/parse errors
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.4 * (2 ** attempt))
        raise SummaryError("summarizer failed: {}".format(last_error))

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """Raw chat call, used by reflection jobs."""
        return self._call(messages)

    def _call(self, messages: List[Dict[str, str]]) -> str:
        if self._transport is not None:
            return self._transport(messages)
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        choices = body.get("choices") or []
        if not choices:
            raise SummaryError("no choices in response")
        content = choices[0].get("message", {}).get("content", "")
        if not isinstance(content, str):
            raise SummaryError("non-text response")
        return content


class AsyncSummarizer:
    """Background summarization worker; results are applied on the main thread."""

    def __init__(
        self,
        summarizer: LLMSummarizer,
        on_result: Callable[[str, str, Optional[Summary]], None],
        *,
        max_queue: int = 256,
    ) -> None:
        self._summarizer = summarizer
        self._on_result = on_result
        self._jobs: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=max_queue)
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._errors: List[str] = []
        self._cond = threading.Condition()
        self._submitted = 0
        self._finished = 0
        self._dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="hippo-summarizer", daemon=True
        )
        self._thread.start()

    def submit(
        self,
        engram_id: str,
        text: str,
        events: List[Event],
        persona: Optional[str] = None,
    ) -> bool:
        try:
            with self._cond:
                self._jobs.put_nowait((engram_id, text, events, persona))
                self._submitted += 1
            return True
        except queue.Full:
            with self._cond:
                self._dropped += 1
            return False

    def _run(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            if job is None:
                self._jobs.task_done()
                return
            engram_id, text, events, persona = job
            summary: Optional[Summary] = None
            try:
                summary = self._summarizer.summarize(events, persona=persona)
            except Exception as exc:  # noqa: BLE001
                self._errors.append("{}: {}".format(engram_id, exc))
            finally:
                self._jobs.task_done()
                self._results.put((engram_id, text, summary))
                with self._cond:
                    self._finished += 1
                    self._cond.notify_all()

    def drain(self) -> int:
        applied = 0
        while True:
            try:
                engram_id, text, summary = self._results.get_nowait()
            except queue.Empty:
                break
            self._on_result(engram_id, text, summary)
            applied += 1
        return applied

    def flush(self, timeout: float = 30.0) -> int:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            target = self._submitted
            while self._finished < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
        return self.drain()

    def take_errors(self) -> List[str]:
        errors = list(self._errors)
        self._errors.clear()
        return errors

    def close(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self.flush(timeout=timeout)
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)

    @property
    def pending(self) -> int:
        with self._cond:
            return max(0, self._submitted - self._finished)

    @property
    def dropped(self) -> int:
        with self._cond:
            return self._dropped
