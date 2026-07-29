"""Lightweight in-process metrics for the chat assistant, to diagnose latency
(e.g. streaming vs classic POST — 'the whole reply takes longer').

Per request we record: mode (stream/post), provider, model, time-to-first-token
(stream only), total time, tool rounds. A structured log line is emitted (the
authoritative record, visible in the add-on log across all workers) plus a small
in-memory ring exposed at GET /assistant/metrics for a quick eyeball. The ring is
per-worker (gunicorn runs a few), so treat it as a recent sample, not a total.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from threading import Lock

_LOG = logging.getLogger("edibl.assistant.metrics")
_RING: deque = deque(maxlen=100)
_LOCK = Lock()


class ChatTimer:
    """Times one chat request. ``first_token()`` on the first streamed delta;
    ``set()`` to record the resolved provider/model; ``done()`` at the end."""

    def __init__(self, mode: str):
        self.mode = mode
        self.provider = ""
        self.model = ""
        self.tool_rounds = 0
        self.ttft_ms = None
        self._t0 = time.monotonic()

    def first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = round((time.monotonic() - self._t0) * 1000, 1)

    def set(self, provider=None, model=None) -> None:
        if provider:
            self.provider = provider
        if model:
            self.model = model

    def tool(self) -> None:
        self.tool_rounds += 1

    def done(self, ok: bool = True) -> dict:
        total_ms = round((time.monotonic() - self._t0) * 1000, 1)
        sample = {
            "mode": self.mode, "provider": self.provider, "model": self.model,
            "toolRounds": self.tool_rounds, "ttftMs": self.ttft_ms,
            "totalMs": total_ms, "ok": ok,
        }
        with _LOCK:
            _RING.append(sample)
        _LOG.info("chat-metric %s", sample)
        return sample


def _agg(samples, mode):
    xs = sorted(s["totalMs"] for s in samples if s["mode"] == mode)
    if not xs:
        return {"count": 0, "avgMs": None, "p50Ms": None, "maxMs": None}
    return {"count": len(xs), "avgMs": round(sum(xs) / len(xs), 1),
            "p50Ms": xs[len(xs) // 2], "maxMs": xs[-1]}


def recent() -> dict:
    """Recent samples (newest first) + a stream-vs-post summary, for the UI/endpoint."""
    with _LOCK:
        samples = list(_RING)
    return {
        "recent": samples[-30:][::-1],
        "summary": {"stream": _agg(samples, "stream"), "post": _agg(samples, "post")},
    }
