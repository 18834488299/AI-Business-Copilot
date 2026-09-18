"""Observable execution traces for local and OpenAI-backed runs."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Trace:
    """Collect one request trace and optionally persist it as JSONL."""

    def __init__(
        self,
        query: str,
        *,
        mode: str,
        session_id: Optional[str] = None,
        trace_dir: Optional[Path] = None,
        persist: bool = False,
    ) -> None:
        self.trace_id = uuid.uuid4().hex
        self.query = query
        self.mode = mode
        self.session_id = session_id
        self.trace_dir = trace_dir
        self.persist = persist
        self.started_at = _utc_now()
        self._started = time.perf_counter()
        self.steps: List[Dict[str, Any]] = []

    def add(self, event: str, **details: Any) -> None:
        self.steps.append(
            {
                "event": event,
                "type": event,
                "step": event,
                "at": _utc_now(),
                **_json_safe(details),
            }
        )

    def finish(self, *, status: str = "ok", **details: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "status": status,
            "started_at": self.started_at,
            "duration_ms": round((time.perf_counter() - self._started) * 1000, 2),
            "steps": self.steps,
        }
        result.update(_json_safe(details))
        if self.persist and self.trace_dir is not None:
            try:
                TraceStore(self.trace_dir).append({"query": self.query, **result})
                result["persisted"] = True
            except OSError as exc:
                result["persisted"] = False
                result["persistence_error"] = str(exc)
        return result


class TraceStore:
    """Thread-safe append-only trace store (one JSON object per line)."""

    _lock = threading.Lock()

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def append(self, record: Dict[str, Any]) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"copilot-{day}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_json_safe(record), ensure_ascii=False) + "\n")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
