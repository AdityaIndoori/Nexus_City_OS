"""
Nexus City OS — Telemetry bus (Pipeline A).

In-process publish/subscribe event bus behind the ``TelemetryBus`` interface.
Production deployments may swap in Kafka (topics partition-mapped by
geographic bounding box) — consumers and producers only see this interface.

Data-integrity guarantee: malformed payloads are routed to a dead-letter
queue (DLQ) topic instead of crashing consumers (MASTER_PROMPT §4).
"""
from __future__ import annotations

import json
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List

DLQ_TOPIC = "dlq"
MAX_DLQ_SIZE = 1000


class TelemetryBus:
    """Thread-safe in-process pub/sub bus with a DLQ."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._dlq: Deque[Dict[str, Any]] = deque(maxlen=MAX_DLQ_SIZE)
        self.published_count = 0
        self.dlq_count = 0

    def subscribe(self, topic: str,
                  handler: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(topic, []).append(handler)

    def publish(self, topic: str, payload: Any) -> bool:
        """Publish a payload. Strings are parsed as JSON; dicts pass through.
        Malformed payloads go to the DLQ. Returns True if delivered."""
        try:
            if isinstance(payload, str):
                message = json.loads(payload)
            elif isinstance(payload, dict):
                message = payload
            else:
                raise TypeError(f"Unsupported payload type {type(payload)!r}")
            if not isinstance(message, dict):
                raise TypeError("Payload must decode to a JSON object")
        except (json.JSONDecodeError, TypeError) as exc:
            self._to_dlq(topic, payload, str(exc))
            return False

        with self._lock:
            handlers = list(self._subscribers.get(topic, []))
            self.published_count += 1

        for handler in handlers:
            try:
                handler(message)
            except Exception as exc:  # noqa: BLE001 — consumer errors -> DLQ
                self._to_dlq(topic, message, f"consumer error: {exc}")
        return True

    def _to_dlq(self, topic: str, payload: Any, error: str) -> None:
        with self._lock:
            self.dlq_count += 1
            self._dlq.append({
                "topic": topic,
                "payload": repr(payload)[:500],
                "error": error,
            })

    def dlq_entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._dlq)