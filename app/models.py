"""Small, JSON-friendly domain models used by the copilot."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class Source:
    id: str
    type: str
    file: str
    citation: str
    description: str = ""
    rows_used: int = 0
    section: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in ("", 0, 0.0)}


@dataclass
class ToolResult:
    name: str
    data: Dict[str, Any]
    summary: str
    sources: List[Source] = field(default_factory=list)
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data": self.data,
            "summary": self.summary,
            "sources": [source.to_dict() for source in self.sources],
            "confidence": round(float(self.confidence), 3),
            "warnings": list(self.warnings),
        }
