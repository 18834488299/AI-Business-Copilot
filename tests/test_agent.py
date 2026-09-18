from app.agent import BusinessCopilot
from types import SimpleNamespace


def tool_names(result):
    return {item["name"] for item in result["tool_calls"]}


def test_five_portfolio_demo_questions_are_grounded():
    agent = BusinessCopilot(mode="local", persist_traces=False)
    session = "portfolio-regression"

    product = agent.chat("本周全国 NovaScan 销量怎么样？", session_id=session)
    assert product["route"] == "product_performance"
    assert "NovaScan" in product["answer"]
    assert "NovaFlow" not in product["answer"]
    assert "query_sales" in tool_names(product)

    comparison = agent.chat("哪个区域下降最多？", session_id=session)
    assert comparison["route"] in {"region_comparison", "region_diagnosis"}
    assert "query_sales" in tool_names(comparison)

    diagnosis = agent.chat("华东为什么下降？", session_id=session)
    assert diagnosis["route"] == "region_diagnosis"
    assert {"query_sales", "query_feedback"}.issubset(tool_names(diagnosis))
    assert "[DATA:sales_weekly.csv]" in diagnosis["citations"]
    assert "[DATA:frontline_feedback.csv]" in diagnosis["citations"]

    policy = agent.chat("新品机构授权规则是什么？", session_id=session)
    assert policy["route"] == "knowledge_qa"
    assert "search_knowledge" in tool_names(policy)
    assert any("07_new_product_authorization.md" in item for item in policy["citations"])

    report = agent.chat("帮我生成本周经营复盘。", session_id=session)
    assert report["route"] == "weekly_report"
    assert all(region in report["answer"] for region in ("华东", "华北", "华南", "西部"))
    assert {"query_sales", "query_feedback"}.issubset(tool_names(report))
    assert report["grounded"] is True


def test_empty_question_is_rejected_without_tool_execution():
    result = BusinessCopilot(mode="local", persist_traces=False).chat("  ")
    assert result["route"] == "validation_error"
    assert result["tool_calls"] == []
    assert result["grounded"] is False


def test_openai_mode_executes_responses_function_call(monkeypatch):
    class FakeResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(
                    id="resp_1",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            name="query_sales",
                            arguments=(
                                '{"date_from":"2026-08-24","date_to":"2026-08-30",'
                                '"metrics":["revenue_cny"],"group_by":[]}'
                            ),
                            call_id="call_1",
                        )
                    ],
                    output_text="",
                )
            return SimpleNamespace(
                id="resp_2",
                output=[],
                output_text="根据工具返回，本周经营数据已有证据支持。",
            )

    fake_responses = FakeResponses()
    fake_client = SimpleNamespace(responses=fake_responses)
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")

    result = BusinessCopilot(
        mode="openai",
        openai_client=fake_client,
        persist_traces=False,
    ).chat("本周全国销售额是多少？")

    assert result["mode"] == "openai"
    assert result["tool_calls"][0]["name"] == "query_sales"
    assert "[DATA:sales_weekly.csv]" in result["answer"]
    assert len(fake_responses.calls) == 2
    assert fake_responses.calls[1]["previous_response_id"] == "resp_1"
    assert fake_responses.calls[1]["input"][0]["type"] == "function_call_output"
