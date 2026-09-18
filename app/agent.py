"""Core NovaMed business agent with local and optional Responses API modes."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .config import PathLike, Settings
from .data import DataCatalog, parse_date
from .models import Source, ToolResult
from .rag import MarkdownKnowledgeBase
from .router import IntentRouter, RouteDecision
from .tools import AnalyticsTools
from .trace import Trace


MessageInput = Union[str, Mapping[str, Any], Sequence[Mapping[str, Any]]]


class BusinessCopilot:
    """Evidence-grounded business assistant.

    The stable public entry point is::

        BusinessCopilot(mode="local").chat(query, session_id=None)

    ``local`` never calls the network and is fully deterministic.  ``auto``
    selects the Responses API only when ``OPENAI_API_KEY`` is present, while
    ``openai`` explicitly requests it.  API/import/runtime failures fail closed
    to the grounded local path.
    """

    VALID_MODES = {"local", "auto", "openai"}

    def __init__(
        self,
        mode: str = "local",
        *,
        data_dir: Optional[PathLike] = None,
        knowledge_dir: Optional[PathLike] = None,
        trace_dir: Optional[PathLike] = None,
        model: Optional[str] = None,
        persist_traces: Optional[bool] = None,
        openai_client: Any = None,
    ) -> None:
        normalized_mode = str(mode or "local").strip().casefold()
        if normalized_mode not in self.VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(self.VALID_MODES)}, got {mode!r}")
        self.mode = normalized_mode
        self.settings = Settings.load(
            data_dir=data_dir,
            knowledge_dir=knowledge_dir,
            trace_dir=trace_dir,
            model=model,
            persist_traces=persist_traces,
        )
        self.catalog = DataCatalog(self.settings.data_dir)
        self.knowledge = MarkdownKnowledgeBase(self.settings.knowledge_dir)
        self.router = IntentRouter(self.catalog)
        self.analytics = AnalyticsTools(self.catalog)
        self._openai_client = openai_client
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._session_lock = threading.Lock()

    def chat(self, query: MessageInput, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Answer one question and return answer, evidence, tools, and trace."""

        text = _message_text(query)
        effective_mode = self._effective_mode()
        trace = Trace(
            text,
            mode=effective_mode,
            session_id=session_id,
            trace_dir=self.settings.trace_dir,
            persist=self.settings.persist_traces,
        )
        if not text:
            trace.add("validation", status="rejected", reason="empty_query")
            final_trace = trace.finish(status="error", grounded=False)
            return {
                "answer": "请输入要分析的经营问题。",
                "route": "validation_error",
                "tools": [],
                "tool_calls": [],
                "sources": [],
                "citations": [],
                "trace": final_trace["steps"],
                "confidence": 0.0,
                "grounded": False,
                "mode": effective_mode,
                "data": {},
                "warnings": ["empty query"],
                "metadata": _trace_metadata(final_trace),
            }

        immediate = self._guardrail_response(text)
        if immediate is not None:
            trace.add("guardrail", route=immediate["route"], reason=immediate.get("reason", "policy"))
            grounded = bool(immediate.get("sources"))
            trace.add("grounding_check", grounded=grounded, source_count=len(immediate.get("sources", [])))
            final_trace = trace.finish(status="ok", grounded=grounded)
            immediate.pop("reason", None)
            immediate["trace"] = final_trace["steps"]
            immediate["metadata"] = _trace_metadata(final_trace)
            immediate["grounded"] = grounded
            immediate["mode"] = effective_mode
            return immediate

        context = self._get_session(session_id)
        decision = self.router.route(text, context=context)
        trace.add(
            "route",
            route=decision.name,
            confidence=round(decision.confidence, 3),
            rationale=decision.rationale,
            params=decision.params,
        )
        boundary = self._boundary_response(decision)
        if boundary is not None:
            trace.add("boundary_check", status="no_matching_data", params=decision.params)
            final_trace = trace.finish(status="ok", grounded=True)
            boundary["trace"] = final_trace["steps"]
            boundary["metadata"] = _trace_metadata(final_trace)
            boundary["grounded"] = True
            boundary["mode"] = effective_mode
            return boundary

        fallback_warning = ""
        if effective_mode == "openai":
            try:
                response = self._run_openai(text, decision, trace)
            except Exception as exc:  # API is optional; the grounded path remains available.
                fallback_warning = f"Responses API 不可用，已回退本地模式：{type(exc).__name__}: {exc}"
                trace.add("openai_fallback", error_type=type(exc).__name__, message=str(exc))
                response = self._run_local(decision, trace)
                response["mode"] = "local-fallback"
        else:
            response = self._run_local(decision, trace)

        if fallback_warning:
            response.setdefault("warnings", []).append(fallback_warning)
        self._set_session(session_id, decision, response)
        grounded = bool(response.get("sources")) and bool(response.get("citations"))
        trace.add(
            "grounding_check",
            grounded=grounded,
            source_count=len(response.get("sources", [])),
            citations=response.get("citations", []),
        )
        response["grounded"] = grounded
        final_trace = trace.finish(status="ok" if response.get("answer") else "error", grounded=grounded)
        response["trace"] = final_trace["steps"]
        response["metadata"] = {
            **dict(response.get("metadata") or {}),
            **_trace_metadata(final_trace),
        }
        response.setdefault("mode", effective_mode)
        return response

    # Friendly aliases for scripts, notebooks, and API adapters.
    run = chat
    query = chat
    ask = chat

    def refresh(self) -> Dict[str, Any]:
        """Reload CSV and Markdown files without recreating the agent."""

        self.catalog.refresh()
        self.knowledge.refresh()
        return self.health()

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok" if self.catalog.sales or self.knowledge.chunks else "degraded",
            "mode": self._effective_mode(),
            "model": self.settings.model if self._effective_mode() == "openai" else None,
            "data": self.catalog.status(),
            "knowledge": self.knowledge.status(),
        }

    def _run_local(self, decision: RouteDecision, trace: Trace) -> Dict[str, Any]:
        started = time.perf_counter()
        public_calls = self._local_tool_plan(trace.query, decision)
        for call in public_calls:
            trace.add("tool_start", tool=call["name"], arguments=call["arguments"])
        if public_calls and public_calls[0]["name"] == "search_knowledge":
            arguments = public_calls[0]["arguments"]
            result = self.knowledge.search_knowledge(
                str(arguments.get("query") or trace.query),
                top_k=int(arguments.get("top_k") or 4),
                filters=arguments.get("filters"),
            )
        elif decision.name == "data_query" and public_calls:
            result = self.analytics.execute(public_calls[0]["name"], public_calls[0]["arguments"])
        else:
            result = self.analytics.execute(decision.name, {**decision.params, "_query": trace.query})
        results = [result]
        knowledge_call = next((item for item in public_calls if item["name"] == "search_knowledge"), None)
        if knowledge_call and result.name != "search_knowledge":
            knowledge_args = knowledge_call["arguments"]
            knowledge_result = self.knowledge.search_knowledge(
                str(knowledge_args.get("query") or trace.query),
                top_k=int(knowledge_args.get("top_k") or 3),
                filters=knowledge_args.get("filters"),
            )
            if knowledge_result.sources:
                results.append(knowledge_result)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        call_records: List[Dict[str, Any]] = []
        for planned in public_calls:
            call = {
                **planned,
                "status": "ok" if result.confidence > 0 else "no_evidence",
                "duration_ms": duration_ms,
            }
            trace.add(
                "tool_end",
                tool=call["name"],
                arguments=call["arguments"],
                status=call["status"],
                duration_ms=duration_ms,
                source_count=sum(len(item.sources) for item in results),
                warnings=[warning for item in results for warning in item.warnings],
            )
            call_records.append(call)
        return self._response_from_results(decision, results, call_records, mode="local")

    def _local_tool_plan(self, text: str, decision: RouteDecision) -> List[Dict[str, Any]]:
        """Describe the stable public tools used behind higher-level routes."""

        lowered = text.casefold()
        if decision.name == "knowledge_qa":
            return [{"name": "search_knowledge", "arguments": self._knowledge_arguments(text)}]
        # A composite diagnosis may mention feedback but still needs sales and
        # knowledge evidence. Only the dedicated data-query route is feedback-only.
        if decision.name == "data_query":
            return [{"name": "query_feedback", "arguments": self._feedback_arguments(text, decision.params)}]
        calls = [{"name": "query_sales", "arguments": self._sales_arguments(text, decision.params)}]
        if "试用转签率" in text:
            calls.append(
                {
                    "name": "search_knowledge",
                    "arguments": self._knowledge_arguments(text),
                }
            )
        if decision.name in {"region_diagnosis", "weekly_report", "business_analysis"}:
            calls.append({"name": "query_feedback", "arguments": self._feedback_arguments(text, decision.params)})
        if decision.name in {"region_diagnosis", "business_analysis"}:
            calls.append(
                {
                    "name": "search_knowledge",
                    "arguments": self._knowledge_arguments(text),
                }
            )
        return calls

    def _data_bounds(self, *, weeks: int = 0) -> Tuple[Optional[str], Optional[str]]:
        dated_rows = [row for row in self.catalog.sales if row.get("_date")]
        if not dated_rows:
            return None, None
        week_starts = sorted({row["_date"] for row in dated_rows})
        if weeks:
            week_starts = week_starts[-weeks:]
        start = min(week_starts)
        selected = [row for row in dated_rows if row["_date"] in set(week_starts)]
        end_values = [parse_date(row.get("date_end")) or row["_date"] for row in selected]
        return start.isoformat(), max(end_values).isoformat()

    def _sales_arguments(self, text: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        lowered = text.casefold()
        if any(token in text for token in ("最近四周", "近四周", "近4周")):
            date_from, date_to = self._data_bounds(weeks=4)
        elif any(token in text for token in ("末周", "最新一周", "最近一周", "本周")):
            date_from, date_to = self._data_bounds(weeks=1)
        else:
            date_from, date_to = self._data_bounds()
        date_from = params.get("start_date") or date_from
        date_to = params.get("end_date") or date_to
        arguments: Dict[str, Any] = {}
        if date_from:
            arguments["date_from"] = date_from
        if date_to:
            arguments["date_to"] = date_to
        if params.get("regions"):
            arguments["regions"] = list(params["regions"])

        product_ids = set(params.get("product_ids") or [])
        org_ids = set(params.get("org_ids") or [])
        for row in self.catalog.sales:
            product = str(row.get("product", ""))
            institution = str(row.get("institution", ""))
            if product and product.casefold() in lowered or (
                product and any(part in lowered for part in re_product_terms(product))
            ):
                if row.get("product_id"):
                    product_ids.add(str(row["product_id"]))
            if institution and institution.casefold() in lowered and row.get("institution_id"):
                org_ids.add(str(row["institution_id"]))
        if any(token in text for token in ("两个产品", "两款产品", "两个在售产品")):
            product_ids.update(str(row["product_id"]) for row in self.catalog.sales if row.get("product_id"))
        product_ids.update(match.upper() for match in _find_product_ids(text))
        org_ids.update(match.upper() for match in _find_org_ids(text))
        if product_ids:
            arguments["product_ids"] = sorted(product_ids)
        if org_ids:
            arguments["org_ids"] = sorted(org_ids)

        metrics: List[str] = []
        metric_cues = (
            ("revenue_cny", ("销售额", "营收", "收入", "业绩")),
            ("target_revenue_cny", ("目标销售额", "销售目标", "目标", "达成率")),
            ("units", ("销量", "套数", "签约量")),
            ("visits", ("拜访",)),
            ("demos", ("演示",)),
            ("trials", ("试用",)),
            ("conversions", ("转签", "转化", "成交")),
            ("returns", ("退订", "退货",)),
        )
        for metric, cues in metric_cues:
            if any(cue in text for cue in cues):
                metrics.append(metric)
        if "销售历史" in text:
            metrics = ["revenue_cny", "units"]
        elif "销售" in text and not metrics:
            metrics = ["revenue_cny"]
        if not metrics:
            metrics = ["revenue_cny", "target_revenue_cny", "units"]
        arguments["metrics"] = metrics

        group_by: List[str] = []
        if "每周" in text or "销售历史" in text or "周趋势" in text:
            group_by.append("week_start")
        if any(cue in text for cue in ("按区域", "各区域", "区域对比", "四个区域")):
            group_by.append("region")
        if any(cue in text for cue in ("按渠道", "渠道汇总", "各渠道")):
            group_by.append("channel")
        if any(cue in text for cue in ("两个产品", "两款产品", "按产品")) or (
            "比较" in text and ("NovaScan" in text or "NovaFlow" in text)
        ):
            group_by.append("product_id")
        if any(cue in text for cue in ("家机构", "机构排名", "Top机构", "top机构")) or (
            "机构" in text and any(cue in text for cue in ("最低", "最高", "最少", "最多"))
        ):
            group_by.append("org_id")
        arguments["group_by"] = list(dict.fromkeys(group_by))
        top_match = _find_top_limit(text)
        if top_match:
            arguments["sort_by"] = "revenue_cny:desc"
            arguments["limit"] = top_match
        return arguments

    def _feedback_arguments(self, text: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        date_from, date_to = self._data_bounds()
        arguments: Dict[str, Any] = {}
        if date_from:
            arguments["date_from"] = date_from
        if date_to:
            arguments["date_to"] = date_to
        if params.get("regions"):
            arguments["regions"] = list(params["regions"])
        sales_args = self._sales_arguments(text, params)
        if sales_args.get("product_ids"):
            arguments["product_ids"] = sales_args["product_ids"]
        for topic in ("系统集成", "采购预算", "部署性能"):
            if topic in text:
                arguments["topics"] = [topic]
        if "负向" in text:
            arguments["sentiments"] = ["负向"]
        if "高或紧急" in text or "高和紧急" in text:
            arguments["urgencies"] = ["高", "紧急"]
        elif "紧急" in text:
            arguments["urgencies"] = ["紧急"]
        arguments["limit"] = 50 if "所有紧急" in text else 20
        return arguments

    @staticmethod
    def _knowledge_arguments(text: str) -> Dict[str, Any]:
        lowered = text.casefold()
        if "在售产品" in text or ("哪" in text and "产品" in text):
            return {
                "query": "NovaScan NMX-01 NovaFlow NMX-02 两款在售产品",
                "top_k": 6,
                "filters": {"file": "01_company_and_products.md"},
            }
        if "授权" in text and any(cue in text for cue in ("新品", "机构", "准入")):
            return {
                "query": "新品机构授权 准入清单 审批 上线复核 安全审查 例外",
                "top_k": 6,
                "filters": {"file": "07_new_product_authorization.md"},
            }
        if "折扣" in text:
            return {
                "query": "折扣 审批 权限 上限",
                "top_k": 3,
                "filters": {"doc_type": "policy"},
            }
        if "试用转签率" in text:
            return {
                "query": "试用转签率 定义 计算",
                "top_k": 3,
                "filters": {"doc_type": "metrics"},
            }
        if any(cue in text for cue in ("紧急反馈", "服务分级", "工单", "升级")):
            return {
                "query": "P0 P1 紧急反馈 工单 升级 响应 恢复 时限",
                "top_k": 4,
                "filters": {"doc_type": "support"},
            }
        mappings = (
            (("部署周期", "使用边界"), "NovaScan 部署周期 使用边界", 3, "product"),
            (("折扣", "审批"), "折扣 审批 权限 上限", 3, "policy"),
            (("目标达成率", "定义"), "目标达成率 定义 计算", 2, "metrics"),
            (("P0", "P1"), "P0 P1 响应 恢复 时限", 3, "support"),
            (("A类机构", "跟进"), "A类机构 跟进 策略", 3, "playbook"),
        )
        for cues, query, top_k, doc_type in mappings:
            if all(cue.casefold() in lowered for cue in cues):
                return {"query": query, "top_k": top_k, "filters": {"doc_type": doc_type}}
        return {"query": text, "top_k": 4, "filters": {}}

    def _run_openai(self, text: str, decision: RouteDecision, trace: Trace) -> Dict[str, Any]:
        client = self._client()
        tools = AnalyticsTools.openai_schemas() + [
            {
                "type": "function",
                "name": "search_knowledge",
                "description": "检索 NovaMed 内部制度、授权分级和 SOP；知识问题必须先调用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        "filters": {"type": "object", "additionalProperties": {"type": "string"}},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        ]
        instructions = (
            "你是 NovaMed 企业经营分析 Agent。所有数字必须先调用 query_sales；所有制度/SOP事实必须先调用 "
            "search_knowledge；原因分析可调用 query_feedback。不得编造。工具证据不足就明确说不足。最终中文回答必须保留"
            "工具结果中的 [DATA:...] 或 [KB:...#...] 引用标记。"
        )
        trace.add("openai_request", model=self.settings.model, tool_count=len(tools))
        response = client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=text,
            tools=tools,
        )
        function_calls = [item for item in getattr(response, "output", []) if getattr(item, "type", "") == "function_call"]
        if not function_calls:
            trace.add("openai_no_tool_call", response_id=getattr(response, "id", None))
            return self._run_local(decision, trace)

        results: List[ToolResult] = []
        call_records: List[Dict[str, Any]] = []
        outputs: List[Dict[str, Any]] = []
        for call in function_calls[:6]:
            name = str(getattr(call, "name", ""))
            try:
                arguments = json.loads(getattr(call, "arguments", "{}") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            started = time.perf_counter()
            trace.add("tool_start", tool=name, arguments=arguments, selected_by="responses_api")
            if name == "search_knowledge":
                result = self.knowledge.search_knowledge(
                    str(arguments.get("query") or text),
                    top_k=int(arguments.get("top_k") or 4),
                    filters=arguments.get("filters"),
                )
            else:
                result = self.analytics.execute(name, arguments)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            results.append(result)
            record = {
                "name": name,
                "arguments": arguments,
                "status": "ok" if result.confidence > 0 else "no_evidence",
                "duration_ms": duration_ms,
                "selected_by": "responses_api",
            }
            call_records.append(record)
            trace.add("tool_end", **record, source_count=len(result.sources))
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": getattr(call, "call_id", getattr(call, "id", "")),
                    "output": json.dumps(result.to_dict(), ensure_ascii=False),
                }
            )

        final = client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            previous_response_id=getattr(response, "id", None),
            input=outputs,
            tools=tools,
        )
        answer = str(getattr(final, "output_text", "") or "").strip()
        composed = self._response_from_results(decision, results, call_records, mode="openai")
        if answer:
            composed["answer"] = _ensure_citations(answer, composed["citations"])
        trace.add("openai_response", response_id=getattr(final, "id", None), answer_length=len(answer))
        return composed

    def _response_from_results(
        self,
        decision: RouteDecision,
        results: Sequence[ToolResult],
        calls: Sequence[Mapping[str, Any]],
        *,
        mode: str,
    ) -> Dict[str, Any]:
        sources = _sources(results)
        citations = [source["citation"] for source in sources if source.get("citation")]
        answer = "\n\n".join(result.summary for result in results if result.summary).strip()
        warnings = [warning for result in results for warning in result.warnings]
        confidence = min(decision.confidence, _combined_confidence(results)) if results else 0.0
        return {
            "answer": answer or "没有足够证据支持回答。",
            "route": decision.name,
            "tools": [dict(call) for call in calls],
            "tool_calls": [dict(call) for call in calls],
            "sources": sources,
            "citations": citations,
            "trace": {},
            "confidence": round(confidence, 3),
            "grounded": bool(sources),
            "mode": mode,
            "data": results[0].data if len(results) == 1 else {result.name: result.data for result in results},
            "warnings": warnings,
        }

    def _client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError("openai package is not installed") from exc
        self._openai_client = OpenAI()
        return self._openai_client

    def _effective_mode(self) -> str:
        if self.mode == "local":
            return "local"
        if os.getenv("OPENAI_API_KEY", "").strip():
            return "openai"
        return "local"

    def _guardrail_response(self, text: str) -> Optional[Dict[str, Any]]:
        lowered = text.casefold()
        if any(term in text for term in ("患者", "病人")) and any(
            term in text for term in ("姓名", "电话", "诊断记录", "病历")
        ):
            return self._immediate(
                "无法提供患者姓名、电话或诊断记录等敏感个人信息；本演示也不包含患者级数据。",
                "refusal_privacy",
                should_refuse=True,
            )
        if any(term in lowered for term in ("管理员密码", "admin password", "生产环境密码", "密钥")):
            return self._immediate(
                "无法提供或推测生产环境管理员密码、密钥等凭证。",
                "refusal_security",
                should_refuse=True,
            )
        if "novascan" in lowered and any(term in text for term in ("确诊", "开药", "处方")):
            source = Source(
                id="KB:01_company_and_products.md#NMX-01",
                type="knowledge_base",
                file="knowledge_base/01_company_and_products.md",
                section="NMX-01：NovaScan AI影像辅助分析平台",
                citation="[KB:01_company_and_products.md#NMX-01]",
                description="NovaScan 临床使用边界",
            )
            return self._immediate(
                "NovaScan 仅用于影像辅助分析，不能替代医生作出诊断，也不能据此自动开药；请由有资质的医务人员判断。 "
                + source.citation,
                "refusal_clinical",
                sources=[source],
                should_refuse=True,
            )
        if "那家医院" in text or "某家医院" in text:
            response = self._immediate(
                "请明确医院或机构名称（例如上海澜庭医院）后，我才能查询对应收入。",
                "clarification_required",
            )
            response["clarification_required"] = True
            return response
        # Do not use a raw ``"成本" in text`` check: the perfectly normal
        # phrase ``生成本周...`` contains those two characters across the word
        # boundary (生[成][本]周) and used to trigger this guardrail by mistake.
        mentions_cost = bool(re.search(r"成本(?=$|[、，,和与费数结分占率是多])", text))
        if "利润" in text or "毛利率" in text or mentions_cost:
            sales_source = self.catalog.data_source(0)
            dictionary = Source(
                id="KB:06_data_dictionary.md#missing-metrics",
                type="knowledge_base",
                file="knowledge_base/06_data_dictionary.md",
                section="缺失指标",
                citation="[KB:06_data_dictionary.md#缺失指标]",
                description="演示数据缺失指标说明",
            )
            return self._immediate(
                "现有销售数据缺少成本、费用字段，因此不能计算实际利润或毛利率。 "
                + " ".join(source.citation for source in sales_source + [dictionary]),
                "fallback_missing_metric",
                sources=sales_source + [dictionary],
            )
        known_products = {"novamed", "novascan", "novaflow"}
        mentioned_products = {
            token.casefold()
            for token in re.findall(
                r"(?<![A-Za-z])Nova[A-Za-z]+(?![A-Za-z])",
                text,
                flags=re.IGNORECASE,
            )
        }
        unknown_products = sorted(mentioned_products - known_products)
        if unknown_products:
            source = Source(
                id="DATA:products.csv",
                type="structured_data",
                file="data/products.csv",
                citation="[DATA:products.csv]",
                description="产品主数据",
            )
            response = self._immediate(
                f"未找到产品 {unknown_products[0]}；请明确是否指 NovaScan 或 NovaFlow。 {source.citation}",
                "clarification_unknown_product",
                sources=[source],
            )
            response["clarification_required"] = True
            return response
        requested_orgs = _find_org_ids(text)
        known_orgs = {str(row.get("institution_id", "")).casefold() for row in self.catalog.sales}
        missing_orgs = [item for item in requested_orgs if item.casefold() not in known_orgs]
        if missing_orgs:
            sources = self.catalog.data_source(0, include_dimensions=True)
            return self._immediate(
                f"未找到机构 {missing_orgs[0]}，因此没有可汇总的销售记录。 "
                + " ".join(source.citation for source in sources),
                "fallback_unknown_institution",
                sources=sources,
            )
        return None

    def _boundary_response(self, decision: RouteDecision) -> Optional[Dict[str, Any]]:
        start = parse_date(decision.params.get("start_date"))
        end = parse_date(decision.params.get("end_date"))
        available_start, available_end = self._data_bounds()
        lower, upper = parse_date(available_start), parse_date(available_end)
        if not lower or not upper or not ((start and start > upper) or (end and end < lower)):
            return None
        sources = self.catalog.data_source(0)
        dictionary = Source(
            id="KB:06_data_dictionary.md#date-range",
            type="knowledge_base",
            file="knowledge_base/06_data_dictionary.md",
            section="NovaMed 演示数据字典",
            citation="[KB:06_data_dictionary.md#数据范围]",
            description="可用数据日期范围",
        )
        sources.append(dictionary)
        requested = f"{decision.params.get('start_date', '')} 至 {decision.params.get('end_date', '')}"
        available = f"{available_start} 至 {available_end}"
        return self._immediate(
            f"请求日期 {requested} 不在数据范围 {available} 内，因此没有数据可作为实际销售额。 "
            + " ".join(source.citation for source in sources),
            "fallback_out_of_range",
            sources=sources,
        )

    @staticmethod
    def _immediate(
        answer: str,
        route: str,
        *,
        sources: Optional[Sequence[Source]] = None,
        should_refuse: bool = False,
    ) -> Dict[str, Any]:
        source_dicts = [source.to_dict() for source in (sources or [])]
        return {
            "answer": answer,
            "route": route,
            "tools": [],
            "tool_calls": [],
            "sources": source_dicts,
            "citations": [item["citation"] for item in source_dicts if item.get("citation")],
            "confidence": 1.0,
            "data": {},
            "warnings": [],
            "should_refuse": should_refuse,
            "trace": [],
        }

    def _get_session(self, session_id: Optional[str]) -> Dict[str, Any]:
        if not session_id:
            return {}
        with self._session_lock:
            return dict(self._sessions.get(session_id, {}))

    def _set_session(self, session_id: Optional[str], decision: RouteDecision, response: Mapping[str, Any]) -> None:
        if not session_id:
            return
        params = dict(decision.params)
        data = response.get("data", {}) if isinstance(response.get("data", {}), Mapping) else {}
        institutions = data.get("institutions", []) if isinstance(data, Mapping) else []
        products = data.get("products", []) if isinstance(data, Mapping) else []
        ranked_institutions = [
            str(item.get("name")) for item in institutions if isinstance(item, Mapping) and item.get("name")
        ]
        if ranked_institutions and not params.get("institutions"):
            params["institutions"] = [ranked_institutions[0]]
        ranked_products = [
            str(item.get("name")) for item in products if isinstance(item, Mapping) and item.get("name")
        ]
        if len(ranked_products) == 1 and not params.get("products"):
            params["products"] = ranked_products
        with self._session_lock:
            self._sessions[session_id] = {
                "route": decision.name,
                "params": params,
                "citations": list(response.get("citations", [])),
                "ranked_institutions": ranked_institutions,
                "ranked_products": ranked_products,
            }
            # Bound memory in long-running demo servers.
            while len(self._sessions) > 500:
                self._sessions.pop(next(iter(self._sessions)))

    @staticmethod
    def _tool_for_route(route: str) -> str:
        return "search_knowledge" if route == "knowledge_qa" else route


def _message_text(value: MessageInput) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("content") or value.get("message") or value.get("query") or "").strip()
    if isinstance(value, Sequence):
        for item in reversed(value):
            if isinstance(item, Mapping) and str(item.get("role", "user")) == "user":
                text = str(item.get("content") or item.get("message") or "").strip()
                if text:
                    return text
    return ""


def _sources(results: Sequence[ToolResult]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for result in results:
        for source in result.sources:
            if source.id not in seen:
                seen.add(source.id)
                sources.append(source.to_dict())
    return sources


def _combined_confidence(results: Sequence[ToolResult]) -> float:
    values = [result.confidence for result in results if result.confidence > 0]
    return sum(values) / len(values) if values else 0.0


def _ensure_citations(answer: str, citations: Sequence[str]) -> str:
    missing = [citation for citation in citations if citation not in answer]
    if not missing:
        return answer
    return answer.rstrip() + "\n\n证据来源：" + " ".join(missing)


def _trace_metadata(trace: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in trace.items() if key != "steps"}


def re_product_terms(product_name: str) -> List[str]:
    terms = [item.casefold() for item in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", product_name)]
    aliases = {
        "novascan": ("novascan", "nova scan"),
        "novaflow": ("novaflow", "nova flow"),
    }
    expanded: List[str] = []
    for term in terms:
        expanded.extend(aliases.get(term, (term,)))
    return expanded


def _find_product_ids(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-z0-9])NMX[-_]?\d{2}(?![A-Za-z0-9])", text, flags=re.IGNORECASE)


def _find_org_ids(text: str) -> List[str]:
    return re.findall(r"(?<![A-Za-z0-9])NM[-_]?H\d{3}(?![A-Za-z0-9])", text, flags=re.IGNORECASE)


def _find_top_limit(text: str) -> int:
    match = re.search(r"(?:top\s*|前\s*|最高的\s*)(\d{1,2})\s*家?", text, flags=re.IGNORECASE)
    return max(1, min(int(match.group(1)), 100)) if match else 0
