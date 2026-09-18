import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_benchmark_has_exactly_120_unique_cases():
    cases = json.loads((ROOT / "benchmark" / "benchmark_120.json").read_text(encoding="utf-8"))
    assert len(cases) == 120
    assert [case["id"] for case in cases] == [f"BM{i:03d}" for i in range(1, 121)]


def test_benchmark_category_distribution():
    cases = json.loads((ROOT / "benchmark" / "benchmark_120.json").read_text(encoding="utf-8"))
    assert Counter(case["category"] for case in cases) == {
        "Knowledge/RAG": 25,
        "数据查询": 30,
        "Tool参数": 20,
        "综合分析": 25,
        "无答案/异常": 10,
        "多轮": 10,
    }


def test_benchmark_contract():
    cases = json.loads((ROOT / "benchmark" / "benchmark_120.json").read_text(encoding="utf-8"))
    expected_keys = {
        "answer_contains",
        "answer_excludes",
        "citations",
        "numeric_checks",
        "tool_calls",
        "clarification_required",
        "should_refuse",
    }
    for case in cases:
        assert set(case) == {"id", "category", "input", "expected", "metadata"}
        assert case["input"]["messages"]
        assert all(item["role"] in {"user", "assistant"} for item in case["input"]["messages"])
        assert set(case["expected"]) == expected_keys
        assert case["metadata"]["difficulty"] in {"easy", "medium", "hard"}
