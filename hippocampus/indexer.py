"""Background embedding indexer.

Embedding is the only part of ``remember()`` that can block on a network or a
local model. The worker computes vectors; the main thread drains finished
results into SQLite. That keeps the SQLite connection single-threaded while
making writes non-blocking.

    hippo = Hippocampus(..., embedder=LocalEmbedder(), async_index=True)
    hippo.remember(event)          # returns immediately
    hippo.close()                  # flushes pending vectors
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, List, Optional, Tuple


class AsyncIndexer:
    def __init__(
        self,
        embedder: Any,
        on_result: Callable[[str, str, str, List[float]], None],
        *,
        max_queue: int = 1024,
        worker_name: str = "hippo-indexer",
    ) -> None:
        self._embedder = embedder
        self._on_result = on_result
        self._jobs: "queue.Queue[Optional[Tuple[str, str, str]]]" = queue.Queue(
            maxsize=max_queue
        )
        self._results: "queue.Queue[Tuple[str, str, str, List[float]]]" = queue.Queue()
        self._errors: List[str] = []
        self._cond = threading.Condition()
        self._submitted = 0
        self._finished = 0
        self._dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=worker_name, daemon=True
        )
        self._thread.start()

    # -- producer ------------------------------------------------------------
    def submit(self, scope: str, engram_id: str, text: str) -> bool:
        if not text.strip():
            return False
        job = (scope, engram_id, text)
        try:
            with self._cond:
                self._jobs.put_nowait(job)
                self._submitted += 1
            return True
        except queue.Full:
            with self._cond:
                self._dropped += 1
            return False

    # -- worker --------------------------------------------------------------
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
            scope, engram_id, text = job
            try:
                vectors = self._embedder.embed([text])
                if vectors:
                    self._results.put((scope, engram_id, text, list(vectors[0])))
            except Exception as exc:  # noqa: BLE001 - provider errors vary
                self._errors.append("{}: {}".format(engram_id, exc))
            finally:
                self._jobs.task_done()
                with self._cond:
                    self._finished += 1
                    self._cond.notify_all()

    # -- consumer (main thread only) -----------------------------------------
    def drain(self) -> int:
        written = 0
        while True:
            try:
                scope, engram_id, text, vector = self._results.get_nowait()
            except queue.Empty:
                break
            self._on_result(scope, engram_id, text, vector)
            written += 1
        return written

    def flush(self, timeout: float = 10.0) -> int:
        """Wait for submitted jobs, then drain their results."""
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

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self.flush(timeout=timeout)
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)

    # -- introspection -------------------------------------------------------
    @property
    def pending(self) -> int:
        with self._cond:
            return max(0, self._submitted - self._finished)

    @property
    def dropped(self) -> int:
        with self._cond:
            return self._dropped
