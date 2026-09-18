"""Small, dependency-free benchmark metrics."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).casefold()


def _tool_records(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    candidates: List[Any] = list(result.get("tools") or []) + list(result.get("tool_calls") or [])
    for event in result.get("trace") or []:
        if not isinstance(event, Mapping):
            continue
        name = event.get("tool") or event.get("tool_name")
        event_type = _norm(event.get("type") or event.get("step") or "")
        if name or "tool" in event_type:
            candidates.append(
                {
                    "name": name or event.get("name"),
                    "arguments": event.get("arguments") or event.get("input") or {},
                }
            )

    for item in candidates:
        if isinstance(item, str):
            records.append({"name": item, "arguments": {}})
        elif isinstance(item, Mapping):
            records.append(
                {
                    "name": item.get("name") or item.get("tool") or item.get("tool_name") or "",
                    "arguments": item.get("arguments") or item.get("args") or item.get("input") or {},
                }
            )
    # A tool can appear both in the public list and in trace; keep the richest record.
    deduped: Dict[str, Dict[str, Any]] = {}
    for record in records:
        key = _norm(record["name"])
        if not key:
            continue
        if key not in deduped or (not deduped[key]["arguments"] and record["arguments"]):
            deduped[key] = record
    return list(deduped.values())


def _arguments_match(actual: Any, expected: Mapping[str, Any]) -> bool:
    if not expected:
        return True
    if isinstance(actual, str):
        try:
            actual = json.loads(actual)
        except json.JSONDecodeError:
            return all(_norm(v) in _norm(actual) for v in expected.values())
    if not isinstance(actual, Mapping):
        return False
    for key, value in expected.items():
        if key not in actual or _norm(actual[key]) != _norm(value):
            return False
    return True


def _numeric_match(answer: str, check: Mapping[str, Any]) -> bool:
    target = float(check["value"])
    tolerance = float(check.get("tolerance", 0.01))
    operator = check.get("operator", "eq")
    numbers: List[float] = []
    for match in re.finditer(r"(-?\d[\d,]*(?:\.\d+)?)\s*(%)?", answer):
        value = float(match.group(1).replace(",", ""))
        numbers.append(value)
        # Benchmarks store ratios as decimals, while user-facing answers usually
        # render the same value as a percentage (0.2 <-> 20%). Score both forms.
        if match.group(2):
            numbers.append(value / 100.0)
    if operator in {"eq", "==", "approx"}:
        return any(abs(number - target) <= tolerance for number in numbers)
    if operator in {"gte", ">="}:
        return any(number >= target - tolerance for number in numbers)
    if operator in {"lte", "<="}:
        return any(number <= target + tolerance for number in numbers)
    if operator in {"gt", ">"}:
        return any(number > target for number in numbers)
    if operator in {"lt", "<"}:
        return any(number < target for number in numbers)
    return False


def _route_family(route: str) -> str:
    value = _norm(route)
    if any(token in value for token in ("knowledge", "rag", "知识", "policy")):
        return "knowledge"
    if any(token in value for token in ("analysis", "diagnosis", "hybrid", "report", "归因", "复盘", "综合")):
        return "analysis"
    if any(
        token in value
        for token in (
            "data",
            "tool",
            "sales",
            "comparison",
            "performance",
            "institution",
            "数据",
            "查询",
        )
    ):
        return "data"
    if any(token in value for token in ("fallback", "clarif", "refus", "handoff", "兜底", "澄清")):
        return "fallback"
    return value


def expected_route(category: str, expected: Mapping[str, Any]) -> str:
    # Routing is scored only where the benchmark provides an unambiguous
    # route family. Composite analysis and refusal cases are evaluated by
    # task/refusal checks instead: they may validly enter through more than
    # one workflow before evidence sufficiency is known.
    if category == "Knowledge/RAG":
        return "knowledge"
    if category == "数据查询":
        return "data"
    if category == "Tool参数":
        names = {str(item.get("name", "")) for item in expected.get("tool_calls", [])}
        if names == {"search_knowledge"}:
            return "knowledge"
        if names:
            return "data"
    return ""


def score_case(case: Mapping[str, Any], result: Mapping[str, Any], latency_ms: float) -> Dict[str, Any]:
    expected = case["expected"]
    answer = str(result.get("answer") or "")
    answer_norm = _norm(answer)
    sources_blob = _norm(
        json.dumps(
            list(result.get("sources") or []) + list(result.get("citations") or []),
            ensure_ascii=False,
        )
    )
    actual_tools = _tool_records(result)

    contains_checks = [(_norm(item) in answer_norm) for item in expected.get("answer_contains", [])]
    excludes_checks = [(_norm(item) not in answer_norm) for item in expected.get("answer_excludes", [])]
    numeric_checks = [_numeric_match(answer, item) for item in expected.get("numeric_checks", [])]
    citation_checks = [
        (_norm(item) in sources_blob or _norm(item) in answer_norm)
        for item in expected.get("citations", [])
    ]

    selection_checks: List[bool] = []
    parameter_checks: List[bool] = []
    for expected_tool in expected.get("tool_calls", []):
        matching = [
            item for item in actual_tools if _norm(item["name"]) == _norm(expected_tool["name"])
        ]
        selection_checks.append(bool(matching))
        parameter_checks.append(
            bool(matching)
            and any(
                _arguments_match(item["arguments"], expected_tool.get("arguments", {}))
                for item in matching
            )
        )

    expected_family = expected_route(case["category"], expected)
    route_ok = None if not expected_family else _route_family(str(result.get("route") or "")) == expected_family

    if expected.get("clarification_required"):
        clarification_ok = bool(result.get("clarification_required")) or any(
            cue in answer for cue in ("请提供", "请明确", "需要您", "请问", "无法确定")
        )
    else:
        clarification_ok = True

    if expected.get("should_refuse"):
        refusal_ok = bool(result.get("should_refuse")) or any(
            cue in answer for cue in ("无法", "不能", "没有相关", "未找到", "超出", "不提供")
        )
    else:
        refusal_ok = True

    required = contains_checks + excludes_checks + numeric_checks + citation_checks
    required += selection_checks + parameter_checks + [clarification_ok, refusal_ok]
    passed = bool(answer.strip()) and all(required or [True])

    return {
        "id": case["id"],
        "category": case["category"],
        "passed": passed,
        "latency_ms": round(latency_ms, 2),
        "route_ok": route_ok,
        "tool_selection_ok": None if not selection_checks else all(selection_checks),
        "parameter_ok": None if not parameter_checks else all(parameter_checks),
        "retrieval_ok": None if not citation_checks else all(citation_checks),
        "clarification_ok": clarification_ok,
        "refusal_ok": refusal_ok,
        "checks": {
            "contains": contains_checks,
            "excludes": excludes_checks,
            "numeric": numeric_checks,
            "citations": citation_checks,
            "tool_selection": selection_checks,
            "parameters": parameter_checks,
        },
        "answer": answer,
        "route": result.get("route"),
        "tools": actual_tools,
    }


def _rate(items: Iterable[Any]) -> Tuple[int, int, float]:
    values = [bool(item) for item in items if item is not None]
    passed = sum(values)
    total = len(values)
    return passed, total, round(passed / total, 4) if total else 0.0


def aggregate(scores: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    latencies = sorted(float(score["latency_ms"]) for score in scores)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1) if latencies else 0
    metrics = {
        "task_completion": _rate(score["passed"] for score in scores),
        "routing_accuracy": _rate(score["route_ok"] for score in scores),
        "retrieval_hit_rate": _rate(score["retrieval_ok"] for score in scores),
        "tool_selection_accuracy": _rate(score["tool_selection_ok"] for score in scores),
        "parameter_accuracy": _rate(score["parameter_ok"] for score in scores),
        "clarification_accuracy": _rate(score["clarification_ok"] for score in scores),
        "refusal_accuracy": _rate(score["refusal_ok"] for score in scores),
    }
    categories: Dict[str, Any] = {}
    for category in sorted({str(score["category"]) for score in scores}):
        category_scores = [score for score in scores if score["category"] == category]
        categories[category] = {
            "task_completion": _rate(score["passed"] for score in category_scores),
            "count": len(category_scores),
        }
    return {
        "total_cases": len(scores),
        "metrics": {
            name: {"passed": value[0], "total": value[1], "rate": value[2]}
            for name, value in metrics.items()
        },
        "latency_ms": {
            "average": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95": round(latencies[p95_index], 2) if latencies else 0.0,
        },
        "by_category": categories,
    }
