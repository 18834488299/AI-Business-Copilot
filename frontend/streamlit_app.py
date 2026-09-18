"""Professional Streamlit UI for the NovaMed AI Business Copilot.

Run from the repository root with::

    streamlit run frontend/streamlit_app.py

The presentation layer deliberately depends on only one application contract:
``app.agent.BusinessCopilot``.  ``run``, ``query`` and ``chat`` style methods
are supported so the UI remains usable while the agent implementation evolves.
"""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlparse

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))


st.set_page_config(
    page_title="NovaMed · AI 商业决策助手",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)


EXAMPLE_QUESTIONS = (
    "本周全国 NovaScan 销量怎么样？",
    "本周哪个区域下降最多？",
    "华东为什么下降？",
    "新品机构授权规则是什么？",
    "帮我生成本周经营复盘。",
)

WELCOME_MESSAGE = {
    "answer": (
        "你好，我是 **NovaMed AI 商业决策助手**。我可以检索内部知识、调用业务分析工具，"
        "并用可核验的来源回答销售、库存、渠道与经营策略问题。\n\n"
        "你可以从下方示例开始，也可以直接输入一个业务问题。"
    ),
    "sources": [],
    "trace": [],
    "metrics": {},
}

SENSITIVE_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)

ROUTE_LABELS = {
    "national_sales_summary": "全国销售概览",
    "region_comparison": "区域对比",
    "product_performance": "产品表现",
    "top_institutions": "机构排名",
    "institution_history": "机构趋势",
    "region_diagnosis": "区域诊断",
    "knowledge_qa": "知识库问答",
    "weekly_report": "周度经营复盘",
    "data_query": "数据分析",
    "validation_error": "输入校验",
}

TOOL_LABELS = {
    "query_sales": "销售数据查询",
    "query_feedback": "一线反馈查询",
    "search_knowledge": "知识库检索",
}


CSS = """
<style>
    :root {
        --ink: #14213d;
        --muted: #64748b;
        --line: #e7ebf2;
        --paper: #ffffff;
        --soft: #f7f9fc;
        --brand: #5b5bd6;
        --brand-dark: #3f3fa8;
        --accent: #13a38b;
    }

    .stApp {
        background:
            radial-gradient(circle at 92% -10%, rgba(91, 91, 214, .10), transparent 28rem),
            #fbfcfe;
        color: var(--ink);
    }

    [data-testid="stSidebar"] {
        background: #10172a;
        border-right: 1px solid rgba(255, 255, 255, .06);
    }

    [data-testid="stSidebar"] * { color: #e9eefb; }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] * { color: #9eabc5; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.12); }
    [data-testid="stSidebar"] .stButton button {
        background: rgba(255, 255, 255, .07);
        border: 1px solid rgba(255, 255, 255, .12);
        color: #eef2ff;
    }

    [data-testid="stSidebar"] .stButton button:hover {
        border-color: rgba(167, 160, 255, .75);
        background: rgba(91, 91, 214, .20);
    }

    .block-container {
        max-width: 1180px;
        padding-top: 2rem;
        padding-bottom: 6rem;
    }

    .sidebar-brand {
        display: flex;
        gap: .75rem;
        align-items: center;
        margin: .15rem 0 1.5rem;
    }

    .brand-mark {
        width: 2.35rem;
        height: 2.35rem;
        display: grid;
        place-items: center;
        border-radius: .72rem;
        color: white;
        font-size: 1.12rem;
        font-weight: 800;
        background: linear-gradient(145deg, #7777ef, #4a4ac0);
        box-shadow: 0 8px 20px rgba(91, 91, 214, .28);
    }

    .brand-title { font-size: .98rem; font-weight: 720; letter-spacing: .01em; }
    .brand-subtitle { font-size: .73rem; color: #9eabc5 !important; margin-top: .1rem; }

    .hero {
        background: linear-gradient(125deg, #ffffff 0%, #f6f5ff 100%);
        border: 1px solid #e8e7f7;
        border-radius: 1.25rem;
        padding: 1.55rem 1.7rem 1.45rem;
        box-shadow: 0 16px 40px rgba(31, 41, 70, .055);
        margin-bottom: 1rem;
    }

    .hero-topline {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
    }

    .eyebrow {
        color: var(--brand);
        font-size: .76rem;
        font-weight: 760;
        letter-spacing: .11em;
        text-transform: uppercase;
        margin-bottom: .45rem;
    }

    .hero h1 {
        color: var(--ink);
        font-size: clamp(1.7rem, 3vw, 2.35rem);
        line-height: 1.16;
        letter-spacing: -.035em;
        margin: 0;
    }

    .hero p {
        color: var(--muted);
        font-size: .96rem;
        line-height: 1.65;
        margin: .72rem 0 0;
        max-width: 48rem;
    }

    .mode-pill {
        flex: 0 0 auto;
        display: inline-flex;
        align-items: center;
        gap: .42rem;
        border-radius: 999px;
        padding: .42rem .72rem;
        background: #edf9f6;
        border: 1px solid #c8eee6;
        color: #087362;
        font-size: .75rem;
        font-weight: 720;
    }

    .mode-dot {
        width: .44rem;
        height: .44rem;
        background: #19aa91;
        border-radius: 50%;
        box-shadow: 0 0 0 .2rem rgba(25, 170, 145, .12);
    }

    .section-label {
        color: #475569;
        font-size: .76rem;
        font-weight: 760;
        letter-spacing: .025em;
        margin: .6rem 0 .35rem;
    }

    div[data-testid="stChatMessage"] {
        border: 1px solid var(--line);
        border-radius: 1rem;
        background: rgba(255, 255, 255, .92);
        padding: .35rem .45rem;
        box-shadow: 0 6px 18px rgba(31, 41, 70, .035);
    }

    div[data-testid="stChatMessage"] + div[data-testid="stChatMessage"] {
        margin-top: .65rem;
    }

    [data-testid="stChatInput"] {
        background: rgba(255,255,255,.96);
        border: 1px solid #dfe4ef;
        box-shadow: 0 12px 36px rgba(28, 37, 63, .12);
    }

    .source-card {
        border-left: 3px solid #8b86e8;
        background: #f8f8fd;
        border-radius: .25rem .7rem .7rem .25rem;
        padding: .65rem .75rem;
        margin: .45rem 0;
    }

    .source-title { color: #293150; font-size: .83rem; font-weight: 720; }
    .source-meta { color: #7c879c; font-size: .7rem; margin: .12rem 0 .28rem; }
    .source-snippet { color: #536077; font-size: .78rem; line-height: 1.55; }

    .trace-step {
        display: grid;
        grid-template-columns: 1.55rem 1fr auto;
        gap: .58rem;
        align-items: start;
        padding: .58rem 0;
        border-bottom: 1px dashed #e6e9f0;
    }

    .trace-index {
        display: grid;
        place-items: center;
        width: 1.45rem;
        height: 1.45rem;
        border-radius: 50%;
        color: #5555bf;
        background: #eeeeff;
        font-size: .67rem;
        font-weight: 760;
    }

    .trace-name { color: #2e3853; font-size: .8rem; font-weight: 700; }
    .trace-summary { color: #707d92; font-size: .73rem; line-height: 1.48; margin-top: .15rem; }
    .trace-status { color: #0b806d; font-size: .68rem; font-weight: 700; white-space: nowrap; }

    .trust-note {
        color: #8490a4;
        font-size: .7rem;
        line-height: 1.55;
        padding: .75rem .85rem;
        border-radius: .65rem;
        background: rgba(255,255,255,.055);
        border: 1px solid rgba(255,255,255,.08);
    }

    .disclaimer {
        color: #8b96aa;
        font-size: .7rem;
        text-align: center;
        margin-top: 1.2rem;
    }

    .stButton button {
        border-radius: .72rem;
        border-color: #e1e5ee;
        min-height: 2.65rem;
    }

    .stButton button:hover {
        border-color: #7777df;
        color: #4c4cb3;
    }

    @media (max-width: 760px) {
        .hero-topline { align-items: flex-start; flex-direction: column; }
        .block-container { padding-top: 1.2rem; }
    }
</style>
"""


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _truncate(value: Any, limit: int = 520) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _plain_mapping(value: Any) -> Optional[Mapping[str, Any]]:
    """Convert dataclasses and Pydantic-like models to ordinary mappings."""

    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                converted = method()
            except (TypeError, ValueError):
                continue
            if isinstance(converted, Mapping):
                return converted
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (str, bytes, Mapping)):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _pick(payload: Mapping[str, Any], names: tuple[str, ...], default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


def normalize_result(result: Any) -> dict[str, Any]:
    """Normalize common agent result formats into the UI view model."""

    payload = _plain_mapping(result)
    if payload is None:
        if isinstance(result, (tuple, list)) and result:
            answer = result[0]
            sources = result[1] if len(result) > 1 else []
            trace = result[2] if len(result) > 2 else []
            return {
                "answer": str(answer),
                "sources": _as_list(sources),
                "trace": _as_list(trace),
                "metrics": {},
                "warnings": [],
                "mode": None,
            }
        return {
            "answer": str(result),
            "sources": [],
            "trace": [],
            "metrics": {},
            "warnings": [],
            "mode": None,
        }

    answer = _pick(
        payload,
        ("answer", "final_answer", "response", "output", "content", "text", "message"),
        "Agent 已完成处理，但没有返回可展示的答案。",
    )
    if isinstance(answer, Mapping):
        answer = _pick(answer, ("content", "text", "answer"), json.dumps(answer, ensure_ascii=False))

    sources = _pick(payload, ("sources", "citations", "references", "evidence", "documents"), [])
    trace = _pick(payload, ("trace", "steps", "tool_calls", "trajectory", "events"), [])
    metrics = _pick(payload, ("metrics", "kpis", "indicators", "stats"), {})

    trace_payload = _plain_mapping(trace)
    if trace_payload is not None and trace_payload.get("steps") is not None:
        trace_steps = _as_list(trace_payload.get("steps"))
        trace_steps.append(
            {
                "event": "request_complete",
                "status": trace_payload.get("status", "ok"),
                "duration_ms": trace_payload.get("duration_ms"),
                "mode": trace_payload.get("mode"),
                "trace_id": trace_payload.get("trace_id"),
            }
        )
        trace = trace_steps

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        if not trace:
            trace = _pick(metadata, ("trace", "steps", "tool_calls"), [])
        if not metrics:
            metrics = _pick(metadata, ("metrics", "kpis", "stats"), {})

    if not trace:
        tools = _as_list(payload.get("tools"))
        trace = [
            {"tool": item, "summary": "业务工具调用完成", "status": "已完成"}
            for item in tools
        ]

    if not isinstance(metrics, Mapping):
        metrics = {}
    metrics = dict(metrics)
    confidence = payload.get("confidence")
    if isinstance(confidence, (int, float)) and "答案置信度" not in metrics:
        score = confidence * 100 if 0 <= confidence <= 1 else confidence
        metrics["答案置信度"] = f"{score:.0f}%"
    route = payload.get("route")
    if route and "处理路径" not in metrics:
        metrics["处理路径"] = ROUTE_LABELS.get(str(route), str(route))
    grounded = payload.get("grounded")
    if isinstance(grounded, bool) and "证据状态" not in metrics:
        metrics["证据状态"] = "已核验" if grounded else "证据不足"
    tools = _as_list(payload.get("tools"))
    if tools and "工具调用" not in metrics:
        metrics["工具调用"] = f"{len(tools)} 次"

    return {
        "answer": str(answer),
        "sources": _as_list(sources),
        "trace": _as_list(trace),
        "metrics": metrics,
        "warnings": _as_list(payload.get("warnings")),
        "mode": payload.get("mode") or (metadata.get("mode") if isinstance(metadata, Mapping) else None),
    }


def _resolve_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    # Streamlit normally has no event loop in the script thread.  A separate
    # thread keeps this adapter safe for hosts that do provide one.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, value).result()


def _invoke_method(
    method: Callable[..., Any],
    question: str,
    session_id: str,
    history: list[dict[str, str]],
) -> Any:
    """Invoke an agent method using its declared parameter names."""

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return _resolve_awaitable(method(question))

    parameters = signature.parameters
    has_var_kwargs = any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())
    prompt_names = ("question", "query", "message", "user_input", "prompt", "text")
    prompt_name = next((name for name in prompt_names if name in parameters), None)
    kwargs: dict[str, Any] = {}

    if prompt_name:
        kwargs[prompt_name] = question
    if "session_id" in parameters or has_var_kwargs:
        kwargs["session_id"] = session_id
    if "conversation_id" in parameters:
        kwargs["conversation_id"] = session_id
    if "history" in parameters:
        kwargs["history"] = history
    if "include_trace" in parameters:
        kwargs["include_trace"] = True
    if "return_trace" in parameters:
        kwargs["return_trace"] = True

    if prompt_name or has_var_kwargs:
        if not prompt_name and has_var_kwargs:
            kwargs["question"] = question
        return _resolve_awaitable(method(**kwargs))

    # Bound methods with a single positional request parameter are common.
    positional = [
        item
        for item in parameters.values()
        if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if positional:
        return _resolve_awaitable(method(question))
    return _resolve_awaitable(method())


@st.cache_resource(show_spinner=False)
def load_copilot() -> Any:
    from app.agent import BusinessCopilot

    requested_mode = os.getenv("COPILOT_MODE", "local").strip().lower()
    mode_aliases = {"cloud": "openai", "online": "openai", "api": "openai", "hybrid": "auto"}
    requested_mode = mode_aliases.get(requested_mode, requested_mode)
    if requested_mode not in {"local", "openai", "auto"}:
        requested_mode = "local"

    try:
        parameters = inspect.signature(BusinessCopilot).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "mode" in parameters:
        return BusinessCopilot(mode=requested_mode)
    return BusinessCopilot()


def ask_copilot(question: str) -> dict[str, Any]:
    copilot = load_copilot()
    messages = st.session_state.messages
    if messages and messages[-1].get("role") == "user" and messages[-1].get("content") == question:
        messages = messages[:-1]
    history = [
        {"role": item["role"], "content": str(item.get("content", ""))}
        for item in messages
        if item.get("role") in {"user", "assistant"}
    ]

    for method_name in ("run", "query", "chat"):
        method = getattr(copilot, method_name, None)
        if callable(method):
            raw = _invoke_method(method, question, st.session_state.session_id, history)
            return normalize_result(raw)

    if callable(copilot):
        return normalize_result(copilot(question))
    raise AttributeError("BusinessCopilot 需要提供 run、query 或 chat 方法。")


def _mode_label() -> tuple[str, str]:
    mode = os.getenv("COPILOT_MODE", "local").strip().lower()
    if mode in {"openai", "cloud", "online", "api"}:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return "本地安全回退", "LOCAL"
        return "云端模型", "OPENAI"
    if mode in {"hybrid", "auto"}:
        if os.getenv("OPENAI_API_KEY", "").strip():
            return "智能路由", "HYBRID"
        return "本地自动模式", "AUTO"
    return "本地确定性", "LOCAL"


def _safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _source_view(source: Any, index: int) -> dict[str, Optional[str]]:
    payload = _plain_mapping(source)
    if payload is None:
        return {
            "title": f"来源 {index}",
            "meta": "内部知识库",
            "snippet": _truncate(source),
            "url": None,
        }

    title = _pick(
        payload,
        ("citation", "title", "name", "document", "file", "source"),
        f"来源 {index}",
    )
    snippet = _pick(
        payload,
        ("snippet", "quote", "description", "content", "text", "excerpt"),
        "",
    )
    source_id = _pick(payload, ("id", "source_id", "doc_id"), "")
    page = _pick(payload, ("page", "page_number", "section"), "")
    score = _pick(payload, ("score", "relevance", "similarity"), None)
    file_name = payload.get("file")

    meta_parts = []
    if source_id and str(source_id) != str(title):
        meta_parts.append(str(source_id))
    if file_name and str(file_name) != str(title):
        meta_parts.append(str(file_name))
    if page:
        meta_parts.append(f"位置 {page}")
    if isinstance(score, (int, float)):
        shown_score = score * 100 if 0 <= score <= 1 else score
        meta_parts.append(f"相关度 {shown_score:.0f}%")

    return {
        "title": _truncate(title, 100),
        "meta": " · ".join(meta_parts) or "内部知识库",
        "snippet": _truncate(snippet) if snippet else "Agent 返回了该来源，但未附摘录。",
        "url": _safe_url(_pick(payload, ("url", "link", "uri"), None)),
    }


def _clean_for_display(value: Any, depth: int = 0) -> Any:
    """Redact credential-like fields before rendering operational trace data."""

    if depth > 4:
        return "…"
    payload = _plain_mapping(value)
    if payload is not None:
        cleaned: dict[str, Any] = {}
        for key, item in payload.items():
            lowered = str(key).lower()
            cleaned[str(key)] = "[已隐藏]" if any(token in lowered for token in SENSITIVE_KEYS) else _clean_for_display(item, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_for_display(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return _truncate(value)
    return value


def _trace_view(
    step: Any,
    index: int,
) -> tuple[str, str, str, Optional[Mapping[str, Any]]]:
    payload = _plain_mapping(step)
    if payload is None:
        return f"步骤 {index}", _truncate(step), "已完成", None

    event = str(payload.get("event", ""))
    event_labels = {
        "validation": "输入校验",
        "route": "意图路由",
        "tool_start": "工具调用开始",
        "tool_end": "工具调用完成",
        "grounding_check": "证据核验",
        "openai_request": "模型请求",
        "openai_response": "模型响应",
        "openai_fallback": "模型降级",
        "openai_no_tool_call": "工具调用检查",
        "request_complete": "请求完成",
    }
    name = event_labels.get(
        event,
        _pick(payload, ("name", "tool", "action", "title", "type", "step"), f"步骤 {index}"),
    )

    summary = _pick(payload, ("summary", "description", "message", "observation", "output", "result"), None)
    if summary is None and event == "route":
        route = payload.get("route", "未识别")
        confidence = payload.get("confidence")
        score = f"，置信度 {float(confidence) * 100:.0f}%" if isinstance(confidence, (int, float)) else ""
        summary = f"选择处理路径 {ROUTE_LABELS.get(str(route), str(route))}{score}"
    elif summary is None and event == "tool_start":
        tool = str(payload.get("tool", "业务工具"))
        summary = f"准备调用 {TOOL_LABELS.get(tool, tool)}"
    elif summary is None and event == "tool_end":
        source_count = payload.get("source_count", 0)
        tool = str(payload.get("tool", "业务工具"))
        summary = f"{TOOL_LABELS.get(tool, tool)}执行结束，获得 {source_count} 个来源"
    elif summary is None and event == "grounding_check":
        grounded = "通过" if payload.get("grounded") else "证据不足"
        summary = f"证据核验{grounded}，来源 {payload.get('source_count', 0)} 条"
    elif summary is None and event == "request_complete":
        summary = f"本次请求以 {payload.get('mode') or '当前'} 模式完成"
    elif summary is None:
        summary = "执行完成"

    status = _pick(payload, ("status", "state"), "已完成")
    status = {
        "ok": "已完成",
        "success": "已完成",
        "completed": "已完成",
        "error": "失败",
        "failed": "失败",
        "no_evidence": "证据不足",
        "rejected": "已拒绝",
    }.get(str(status).lower(), status)
    duration = _pick(payload, ("duration_ms", "latency_ms", "elapsed_ms"), None)
    if isinstance(duration, (int, float)):
        status = f"{status} · {duration:.0f} ms"
    return _truncate(name, 80), _truncate(summary, 240), _truncate(status, 40), payload


def render_metrics(metrics: Mapping[str, Any]) -> None:
    if not metrics:
        return
    shown = list(metrics.items())[:4]
    columns = st.columns(len(shown))
    for column, (label, raw_value) in zip(columns, shown):
        value = raw_value
        delta = None
        if isinstance(raw_value, Mapping):
            value = _pick(raw_value, ("value", "current", "amount"), "—")
            delta = _pick(raw_value, ("delta", "change", "trend"), None)
        column.metric(str(label), str(value), None if delta is None else str(delta))


def render_sources(sources: list[Any]) -> None:
    with st.expander(f"来源与依据 · {len(sources)} 条", expanded=False):
        if not sources:
            st.caption("本次回答未返回可引用来源；重要决策请补充核验。")
            return
        for index, source in enumerate(sources, 1):
            item = _source_view(source, index)
            link = ""
            if item["url"]:
                link = f' · <a href="{_escape(item["url"])}" target="_blank">查看原文</a>'
            st.markdown(
                (
                    '<div class="source-card">'
                    f'<div class="source-title">[{index}] {_escape(item["title"])}</div>'
                    f'<div class="source-meta">{_escape(item["meta"])}{link}</div>'
                    f'<div class="source-snippet">{_escape(item["snippet"])}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )


def render_trace(trace: list[Any]) -> None:
    with st.expander(f"执行轨迹 · {len(trace)} 步", expanded=False):
        st.caption("这里只展示工具调用与检索步骤，不展示模型的私有思维过程。")
        if not trace:
            st.caption("本次回答未返回执行轨迹。")
            return
        for index, step in enumerate(trace, 1):
            name, summary, status, payload = _trace_view(step, index)
            st.markdown(
                (
                    '<div class="trace-step">'
                    f'<div class="trace-index">{index}</div>'
                    '<div>'
                    f'<div class="trace-name">{_escape(name)}</div>'
                    f'<div class="trace-summary">{_escape(summary)}</div>'
                    "</div>"
                    f'<div class="trace-status">{_escape(status)}</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            if payload:
                details = {
                    str(key): value
                    for key, value in payload.items()
                    if key
                    not in {
                        "event",
                        "name",
                        "tool",
                        "action",
                        "title",
                        "summary",
                        "description",
                        "status",
                        "state",
                    }
                }
                if details:
                    with st.popover(f"步骤 {index} 详情"):
                        st.json(_clean_for_display(details), expanded=False)


def render_assistant(payload: Mapping[str, Any]) -> None:
    st.markdown(str(payload.get("answer", "")))
    mode = payload.get("mode")
    if mode:
        mode_name = {
            "local": "本地确定性",
            "openai": "云端模型",
            "auto": "智能路由",
            "local-fallback": "本地安全回退",
        }.get(str(mode).lower(), str(mode))
        st.caption(f"本次回答模式：{mode_name}")
    for warning in _as_list(payload.get("warnings")):
        if warning:
            st.warning(str(warning))
    render_metrics(payload.get("metrics", {}))
    render_sources(_as_list(payload.get("sources")))
    render_trace(_as_list(payload.get("trace")))


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="brand-mark">N</div>
                <div>
                    <div class="brand-title">NovaMed Copilot</div>
                    <div class="brand-subtitle">商业决策智能体</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("＋ 新建会话", use_container_width=True):
            st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]
            st.session_state.session_id = uuid.uuid4().hex
            st.rerun()

        st.markdown("---")
        st.caption("当前工作空间")
        st.markdown("**商业分析 · 教学演示**")
        st.caption(f"会话编号  {st.session_state.session_id[:8].upper()}")

        st.markdown("---")
        st.caption("能力")
        st.markdown("🔎  知识库检索")
        st.markdown("▦  结构化数据分析")
        st.markdown("✦  建议生成与证据引用")
        st.markdown("⌁  工具调用轨迹")

        st.markdown("---")
        st.markdown(
            """
            <div class="trust-note">
                <strong>演示环境</strong><br/>
                页面中的公司、产品、客户及经营数据均为虚构，仅用于作品展示，不构成真实业务建议。
            </div>
            """,
            unsafe_allow_html=True,
        )


def initialize_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex
    if "messages" not in st.session_state:
        st.session_state.messages = [{"role": "assistant", "content": WELCOME_MESSAGE}]


def main() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    initialize_state()
    render_sidebar()
    mode_name, mode_code = _mode_label()

    st.markdown(
        f"""
        <section class="hero">
            <div class="hero-topline">
                <div>
                    <div class="eyebrow">AI Business Copilot</div>
                    <h1>让每个经营结论都有数据依据</h1>
                </div>
                <div class="mode-pill"><span class="mode-dot"></span>{_escape(mode_code)} · {_escape(mode_name)}</div>
            </div>
            <p>面向销售、库存与渠道决策的可追溯 AI Agent：理解问题、选择工具、检索证据，再交付可执行建议。</p>
        </section>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="section-label">你可以这样问</div>', unsafe_allow_html=True)
    example_columns = st.columns(2)
    selected_example: Optional[str] = None
    for index, question in enumerate(EXAMPLE_QUESTIONS):
        with example_columns[index % 2]:
            if st.button(question, key=f"example-{index}", use_container_width=True):
                selected_example = question

    st.markdown('<div class="section-label">对话</div>', unsafe_allow_html=True)
    for message in st.session_state.messages:
        role = message.get("role", "assistant")
        avatar = "✨" if role == "assistant" else "👤"
        with st.chat_message(role, avatar=avatar):
            if role == "assistant":
                render_assistant(message.get("content", {}))
            else:
                st.markdown(str(message.get("content", "")))

    typed_question = st.chat_input("输入业务问题，例如：哪些区域需要优先补货？")
    question = selected_example or typed_question
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user", avatar="👤"):
            st.markdown(question)

        with st.chat_message("assistant", avatar="✨"):
            try:
                with st.spinner("正在理解问题、调用工具并核验来源…"):
                    response = ask_copilot(question)
                render_assistant(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
            except Exception as exc:  # keep the UI useful when integration is incomplete
                st.error("Agent 暂时无法完成这次请求。请确认应用配置后重试。")
                with st.expander("查看诊断信息"):
                    st.code(f"{type(exc).__name__}: {exc}")

    st.markdown(
        '<div class="disclaimer">演示系统 · 所有数据均为虚构 · 重要业务判断请进行人工复核</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
