"""FastAPI entry point.

Run with::

    uvicorn api.main:app --reload
"""

import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import BusinessCopilot


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2_000)
    session_id: Optional[str] = None
    mode: Optional[str] = Field(default=None, pattern="^(local|openai)$")


class ChatResponse(BaseModel):
    answer: str
    route: str
    tools: list = Field(default_factory=list)
    tool_calls: list = Field(default_factory=list)
    sources: list = Field(default_factory=list)
    citations: list = Field(default_factory=list)
    trace: list = Field(default_factory=list)
    confidence: float = 0.0
    grounded: bool = False
    mode: str = "local"
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: list = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


app = FastAPI(
    title="NovaMed AI Business Copilot API",
    description="Evidence-grounded commercial analytics with RAG and business tools.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_agents: Dict[str, BusinessCopilot] = {}


def get_agent(mode: Optional[str] = None) -> BusinessCopilot:
    selected = mode or os.getenv("COPILOT_MODE", "local")
    if selected == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY is required for openai mode; use local mode instead.",
        )
    if selected not in _agents:
        _agents[selected] = BusinessCopilot(mode=selected)
    return _agents[selected]


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "default_mode": os.getenv("COPILOT_MODE", "local")}


@app.get("/api/examples")
def examples() -> Dict[str, list]:
    return {
        "examples": [
            "本周全国 NovaScan 销量怎么样？",
            "哪个区域下降最多？",
            "华东为什么下降？",
            "新品机构授权规则是什么？",
            "帮我生成本周经营复盘。",
        ]
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> Dict[str, Any]:
    try:
        result = get_agent(request.mode).chat(
            request.message,
            session_id=request.session_id,
        )
    except HTTPException:
        raise
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - last-resort API boundary
        raise HTTPException(status_code=500, detail="Agent execution failed") from exc

    result.setdefault("route", "unknown")
    result.setdefault("tools", [])
    result.setdefault("tool_calls", result.get("tools", []))
    result.setdefault("sources", [])
    result.setdefault("citations", [])
    result.setdefault("trace", [])
    result.setdefault("confidence", 0.0)
    result.setdefault("grounded", False)
    result.setdefault("mode", request.mode or os.getenv("COPILOT_MODE", "local"))
    result.setdefault("data", {})
    result.setdefault("warnings", [])
    result.setdefault("metadata", {})
    return result
