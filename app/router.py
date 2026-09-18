"""Deterministic Chinese/English intent routing and entity extraction."""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from .data import DataCatalog


@dataclass
class RouteDecision:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IntentRouter:
    """Route the eight portfolio-demo use cases without an LLM dependency."""

    ROUTES = (
        "national_sales_summary",
        "region_comparison",
        "product_performance",
        "top_institutions",
        "institution_history",
        "region_diagnosis",
        "knowledge_qa",
        "weekly_report",
        "data_query",
        "business_analysis",
    )
    COMMON_REGIONS = (
        "华东",
        "华南",
        "华北",
        "华中",
        "西南",
        "西北",
        "西部",
        "东北",
    )

    def __init__(self, catalog: Optional[DataCatalog] = None) -> None:
        self.catalog = catalog

    def route(
        self,
        query: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
    ) -> RouteDecision:
        text = str(query or "").strip()
        lowered = text.casefold()
        params = self._extract_params(text)
        context = dict(context or {})

        # Only genuinely elliptical follow-ups inherit filters.  A fresh intent
        # such as "本周经营复盘" must never be polluted by an earlier entity.
        elliptical = any(cue in text for cue in ("那", "它", "另一个", "只看", "改成", "呢", "超过", "第二名"))
        if len(text) <= 24 and elliptical:
            prior = context.get("params", {}) if isinstance(context.get("params", {}), Mapping) else {}
            for key in ("regions", "products", "institutions"):
                if not params.get(key) and prior.get(key):
                    params[key] = list(prior[key])
            if "另一个" in text and prior.get("products") and self.catalog:
                previous = set(prior["products"])
                alternatives = [item for item in self.catalog.entities().get("product", []) if item not in previous]
                if alternatives:
                    params["products"] = alternatives[:1]
            if "第二名" in text and context.get("ranked_institutions"):
                ranked = list(context["ranked_institutions"])
                if len(ranked) >= 2:
                    params["institutions"] = [ranked[1]]

        def has(*words: str) -> bool:
            return any(word.casefold() in lowered for word in words)

        if has("周报", "weekly report", "周度报告", "本周经营", "经营简报"):
            return RouteDecision("weekly_report", params, 0.99, "命中周报/经营简报关键词")
        composite_cues = (
            "管理层",
            "总结全国",
            "概括8周",
            "销售漏斗",
            "情绪分布",
            "汇总紧急",
            "排优先级",
            "结合其反馈",
            "改善方向",
            "折扣水平",
            "直销与合作伙伴",
            "三类机构",
            "评估退订",
            "最初两周",
            "目标缺口",
            "最常见的主题",
            "结合紧急反馈",
            "经营摘要",
            "主要风险",
            "客户声音",
        )
        if has(*composite_cues):
            return RouteDecision("business_analysis", params, 0.96, "跨数据源综合经营分析意图")

        knowledge_specific = (
            has("真实公司", "在售产品", "定位是什么", "业务场景", "目录价", "字段的口径")
            or (has("折扣") and not has("平均折扣率", "折扣水平"))
            or has("怎么计算", "是否已经从", "经营策略", "连续两周低于目标", "响应和恢复", "响应时限")
            or (has("升级为p", "反馈为紧急") and has("怎么", "哪些", "应"))
            or has("每行代表", "以哪个文件为准", "授权规则", "忽略知识库")
        )
        if knowledge_specific:
            return RouteDecision("knowledge_qa", params, 0.98, "命中产品/指标/制度知识问答")
        if has("反馈", "一线声音", "客户声音") and has(
            "查", "看", "分析", "汇总", "总结", "分布", "多少", "负向", "主题", "部署", "采购", "集成"
        ):
            return RouteDecision("data_query", params, 0.97, "命中结构化一线反馈查询")
        if has("哪个区域", "哪一个区域") and has("下降", "下滑", "增长"):
            return RouteDecision("region_comparison", params, 0.95, "区域趋势排名意图")
        if has("为什么", "原因", "诊断", "归因", "下滑", "下降", "落后", "没达标", "未达标", "怎么改善") and (
            params.get("regions") or has("区域", "大区", "销售")
        ):
            return RouteDecision("region_diagnosis", params, 0.96, "区域问题与原因分析组合意图")
        if has(
            "授权",
            "审批",
            "分级",
            "sop",
            "标准作业",
            "标准流程",
            "合规",
            "制度",
            "操作规范",
            "知识库",
            "政策",
            "使用边界",
            "部署周期",
            "响应时限",
            "跟进策略",
            "定义",
        ):
            return RouteDecision("knowledge_qa", params, 0.98, "命中制度/SOP/分级知识关键词")
        if has("历史", "趋势", "走势", "历周", "变化") and (
            params.get("institutions") or has("机构", "医院", "客户")
        ):
            return RouteDecision("institution_history", params, 0.95, "机构实体与历史趋势组合意图")
        if has("top", "排行", "排名", "前", "最高", "最低", "头部") and has(
            "机构", "医院", "客户", "account", "org"
        ):
            return RouteDecision("top_institutions", params, 0.96, "命中机构排名意图")
        if has("哪个区域", "区域的目标达成率") and has("最高", "最低", "最好", "最弱"):
            return RouteDecision("region_comparison", params, 0.95, "区域目标达成率排名意图")
        if has("对比", "比较", "vs", " versus ", "差异") and (
            len(params.get("regions", [])) >= 1 or has("区域", "大区")
        ):
            return RouteDecision("region_comparison", params, 0.95, "命中区域对比意图")
        if params.get("products") or params.get("product_ids") or has("产品", "品类", "product", "sku", "novascan", "novaflow"):
            return RouteDecision("product_performance", params, 0.90, "命中产品表现意图")
        if has("机构", "医院", "客户") and has("历史", "表现", "销售", "情况"):
            return RouteDecision("institution_history", params, 0.82, "机构经营查询")
        if has(
            "销售",
            "营收",
            "收入",
            "全国",
            "整体",
            "总览",
            "摘要",
            "概览",
            "业绩",
            "卖出",
            "多少套",
            "套数",
            "增长率",
            "退订",
            "转签",
            "利润",
            "毛利率",
        ):
            return RouteDecision("national_sales_summary", params, 0.90, "命中销售总览意图")

        # If a follow-up is genuinely elliptical, retain the prior route.
        previous_route = str(context.get("route", ""))
        if previous_route in self.ROUTES:
            return RouteDecision(previous_route, params, 0.72, "短追问沿用会话中的上一业务意图")
        return RouteDecision("knowledge_qa", params, 0.58, "未命中分析规则，先检索内部知识库")

    def _extract_params(self, query: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        entities = self.catalog.entities() if self.catalog else {}
        query_folded = query.casefold()
        field_to_param = {
            "region": "regions",
            "province": "provinces",
            "city": "cities",
            "product": "products",
            "institution": "institutions",
            "tier": "tiers",
        }
        for field, param in field_to_param.items():
            matches = [value for value in entities.get(field, []) if value.casefold() in query_folded]
            if field == "product":
                for value in entities.get(field, []):
                    aliases = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", value)
                    if any(alias.casefold() in query_folded for alias in aliases):
                        matches.append(value)
            if matches:
                # Keep longest matches and prevent a city fragment duplicating an org.
                params[param] = list(dict.fromkeys(matches))
        common_regions = [value for value in self.COMMON_REGIONS if value.casefold() in query_folded]
        if common_regions:
            params["regions"] = list(dict.fromkeys(params.get("regions", []) + common_regions))

        # IDs are useful to the public tools even if a dimension lookup is unavailable.
        product_ids = re.findall(r"(?<![A-Za-z0-9])(?:NMX|P|SKU)[-_]?\d{2,}(?![A-Za-z0-9])", query, flags=re.IGNORECASE)
        org_ids = re.findall(r"(?<![A-Za-z0-9])(?:NM[-_]?H|O|ORG|H)[-_]?\d{2,}(?![A-Za-z0-9])", query, flags=re.IGNORECASE)
        if product_ids:
            params["product_ids"] = [item.upper() for item in product_ids]
        if org_ids:
            params["org_ids"] = [item.upper() for item in org_ids]

        top = re.search(r"(?:top\s*|前\s*)(\d{1,2})", query, flags=re.IGNORECASE)
        if top:
            params["top_k"] = max(1, min(int(top.group(1)), 50))
        else:
            params["top_k"] = 5

        recent = re.search(r"最近\s*(\d{1,2})\s*周", query)
        if recent:
            params["relative_weeks"] = max(1, min(int(recent.group(1)), 104))
        elif "本周" in query or "最新一周" in query or "最近一周" in query:
            params["relative_weeks"] = 1
        elif "近四周" in query or "近4周" in query:
            params["relative_weeks"] = 4

        iso_dates = re.findall(r"(?<!\d)(20\d{2})[-/.年](\d{1,2})(?:[-/.月](\d{1,2})日?)?", query)
        if len(iso_dates) >= 2:
            params["start_date"] = self._parts_to_date(iso_dates[0], end=False)
            params["end_date"] = self._parts_to_date(iso_dates[1], end=True)
        elif len(iso_dates) == 1:
            parts = iso_dates[0]
            if parts[2]:
                params["start_date"] = self._parts_to_date(parts, end=False)
                params["end_date"] = self._parts_to_date(parts, end=False)
            else:
                params["start_date"] = self._parts_to_date(parts, end=False)
                params["end_date"] = self._parts_to_date(parts, end=True)

        quarter = re.search(r"(20\d{2})年?第?([一二三四1-4])季度", query)
        if quarter and not params.get("start_date"):
            quarter_number = {"一": 1, "二": 2, "三": 3, "四": 4}.get(quarter.group(2), int(quarter.group(2)) if quarter.group(2).isdigit() else 1)
            start_month = (quarter_number - 1) * 3 + 1
            end_month = start_month + 2
            year = int(quarter.group(1))
            params["start_date"] = date(year, start_month, 1).isoformat()
            params["end_date"] = date(year, end_month, calendar.monthrange(year, end_month)[1]).isoformat()

        week = re.search(r"(?:(20\d{2})年?\s*第?\s*|第\s*)(\d{1,2})\s*周", query)
        if week and not params.get("start_date"):
            year = int(week.group(1)) if week.group(1) else self._default_year()
            try:
                monday = date.fromisocalendar(year, int(week.group(2)), 1)
                params["start_date"] = monday.isoformat()
                params["end_date"] = date.fromordinal(monday.toordinal() + 6).isoformat()
            except ValueError:
                pass
        return params

    def _default_year(self) -> int:
        if self.catalog:
            _, latest = self.catalog.date_range()
            if latest:
                return latest.year
        return date.today().year

    @staticmethod
    def _parts_to_date(parts: Any, *, end: bool) -> str:
        year, month, day = int(parts[0]), int(parts[1]), parts[2]
        day_number = int(day) if day else (calendar.monthrange(year, month)[1] if end else 1)
        return date(year, month, day_number).isoformat()


def route_query(
    query: str,
    catalog: Optional[DataCatalog] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Functional wrapper convenient for notebooks and unit tests."""

    return IntentRouter(catalog).route(query, context=context).to_dict()
