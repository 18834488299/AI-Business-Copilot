"""Configuration helpers shared by the agent modules."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, os.PathLike]


def project_root() -> Path:
    """Return the repository root, independent of the caller's cwd."""

    return Path(__file__).resolve().parents[1]


def _resolve(value: Optional[PathLike], default: str) -> Path:
    path = Path(value).expanduser() if value is not None else project_root() / default
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    knowledge_dir: Path
    trace_dir: Path
    model: str
    persist_traces: bool

    @classmethod
    def load(
        cls,
        *,
        data_dir: Optional[PathLike] = None,
        knowledge_dir: Optional[PathLike] = None,
        trace_dir: Optional[PathLike] = None,
        model: Optional[str] = None,
        persist_traces: Optional[bool] = None,
    ) -> "Settings":
        if persist_traces is None:
            value = os.getenv("COPILOT_PERSIST_TRACES", "1").strip().lower()
            persist_traces = value not in {"0", "false", "no", "off"}
        return cls(
            data_dir=_resolve(data_dir or os.getenv("COPILOT_DATA_DIR"), "data"),
            knowledge_dir=_resolve(
                knowledge_dir or os.getenv("COPILOT_KNOWLEDGE_DIR"),
                "knowledge_base",
            ),
            trace_dir=_resolve(trace_dir or os.getenv("COPILOT_TRACE_DIR"), "traces"),
            model=model or os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            persist_traces=bool(persist_traces),
        )
