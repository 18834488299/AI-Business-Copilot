from evaluation.metrics import aggregate, score_case


def test_case_scoring_covers_route_tool_parameter_and_citation():
    case = {
        "id": "BM000",
        "category": "数据查询",
        "expected": {
            "answer_contains": ["华东"],
            "answer_excludes": ["真实客户"],
            "citations": ["sales_weekly.csv"],
            "numeric_checks": [{"metric": "环比", "operator": "eq", "value": -18, "tolerance": 0.1}],
            "tool_calls": [{"name": "query_sales", "arguments": {"region": "华东"}}],
            "clarification_required": False,
            "should_refuse": False,
        },
    }
    result = {
        "answer": "华东环比 -18%，结论仅来自模拟数据。",
        "route": "region_comparison",
        "tool_calls": [{"name": "query_sales", "arguments": {"region": "华东"}}],
        "citations": ["sales_weekly.csv"],
    }
    score = score_case(case, result, 12.5)
    assert score["passed"] is True
    assert score["route_ok"] is True
    assert score["tool_selection_ok"] is True
    assert score["parameter_ok"] is True
    assert score["retrieval_ok"] is True


def test_aggregate_ignores_non_applicable_metrics():
    summary = aggregate(
        [
            {
                "category": "Knowledge/RAG",
                "passed": True,
                "route_ok": True,
                "tool_selection_ok": None,
                "parameter_ok": None,
                "retrieval_ok": True,
                "clarification_ok": True,
                "refusal_ok": True,
                "latency_ms": 10,
            }
        ]
    )
    assert summary["metrics"]["task_completion"]["rate"] == 1.0
    assert summary["metrics"]["tool_selection_accuracy"]["total"] == 0


def test_ratio_ground_truth_matches_human_readable_percentage():
    case = {
        "id": "BM000",
        "category": "Knowledge/RAG",
        "expected": {
            "answer_contains": [],
            "answer_excludes": [],
            "citations": [],
            "numeric_checks": [
                {"metric": "最大折扣率", "operator": "eq", "value": 0.2, "unit": "比例"}
            ],
            "tool_calls": [],
            "clarification_required": False,
            "should_refuse": False,
        },
    }
    score = score_case(case, {"answer": "任何交易的折扣绝对上限为20%。", "route": "knowledge_qa"}, 1)
    assert score["passed"] is True
