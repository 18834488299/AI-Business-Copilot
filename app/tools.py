"""Auditable business tools backed only by local CSV facts."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .data import ALIASES, CSVTable, DataCatalog, _find_column, parse_date, parse_number
from .models import Source, ToolResult


METRIC_FIELDS = {
    "revenue": "amount",
    "revenue_cny": "amount",
    "sales": "amount",
    "销售额": "amount",
    "target": "target",
    "target_revenue_cny": "target",
    "目标": "target",
    "units": "units",
    "销量": "units",
    "visits": "visits",
    "demos": "demos",
    "trials": "trials",
    "conversions": "conversions",
    "returns": "returns",
}

DIMENSION_FIELDS = {
    "region": "region",
    "区域": "region",
    "product": "product",
    "product_name": "product",
    "product_id": "product_id",
    "产品": "product",
    "org": "institution",
    "org_name": "institution",
    "org_id": "institution_id",
    "institution": "institution",
    "机构": "institution",
    "week": "date",
    "week_start": "date",
    "date": "date",
    "日期": "date",
    "channel": "channel",
    "渠道": "channel",
    "tier": "tier",
    "机构分级": "tier",
}


class AnalyticsTools:
    """Business calculations and portfolio-level analysis primitives."""

    def __init__(self, catalog: DataCatalog) -> None:
        self.catalog = catalog

    def execute(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> ToolResult:
        args = dict(arguments or {})
        registry: Dict[str, Callable[..., ToolResult]] = {
            "query_sales": self.query_sales,
            "query_feedback": self.query_feedback,
            "national_sales_summary": self.national_sales_summary,
            "region_comparison": self.region_comparison,
            "product_performance": self.product_performance,
            "top_institutions": self.top_institutions,
            "institution_history": self.institution_history,
            "region_diagnosis": self.region_diagnosis,
            "weekly_report": self.weekly_report,
            "business_analysis": self.business_analysis,
        }
        if name not in registry:
            return ToolResult(
                name=name,
                data={},
                summary=f"未知工具：{name}",
                confidence=0.0,
                warnings=[f"Tool not registered: {name}"],
            )
        return registry[name](**args)

    def query_sales(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        regions: Optional[Sequence[str]] = None,
        product_ids: Optional[Sequence[str]] = None,
        org_ids: Optional[Sequence[str]] = None,
        metrics: Optional[Sequence[str]] = None,
        group_by: Optional[Sequence[str]] = None,
        sort_by: Optional[str] = None,
        limit: Optional[int] = None,
        products: Optional[Sequence[str]] = None,
        institutions: Optional[Sequence[str]] = None,
        relative_weeks: Optional[int] = None,
        **_: Any,
    ) -> ToolResult:
        """General allow-listed aggregation tool used by the Agent interface."""

        if isinstance(regions, str):
            regions = [regions]
        if isinstance(product_ids, str):
            product_ids = [product_ids]
        if isinstance(org_ids, str):
            org_ids = [org_ids]
        if isinstance(products, str):
            products = [products]
        if isinstance(institutions, str):
            institutions = [institutions]
        filters = {
            "start_date": date_from,
            "end_date": date_to,
            "regions": list(regions or []),
            "products": list(products or []),
            "institutions": list(institutions or []),
            "relative_weeks": relative_weeks,
        }
        rows = self.catalog.filter_sales(filters)
        if product_ids:
            wanted = {str(item).casefold() for item in product_ids}
            rows = [row for row in rows if str(row.get("product_id", "")).casefold() in wanted]
        if org_ids:
            wanted = {str(item).casefold() for item in org_ids}
            rows = [row for row in rows if str(row.get("institution_id", "")).casefold() in wanted]
        if not rows:
            return self._empty("query_sales", filters)

        requested_metrics = [METRIC_FIELDS.get(str(item).casefold(), "") for item in (metrics or [])]
        requested_metrics = [item for item in requested_metrics if item]
        if not requested_metrics:
            requested_metrics = ["amount", "target", "units"]
        dimensions = [DIMENSION_FIELDS.get(str(item).casefold(), "") for item in (group_by or [])]
        dimensions = [item for item in dimensions if item]
        totals = _metrics(rows)
        result_rows: List[Dict[str, Any]] = []
        if dimensions:
            grouped: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[tuple(str(row.get(field, "")) for field in dimensions)].append(row)
            for keys, group in grouped.items():
                item = {field: key for field, key in zip(dimensions, keys)}
                values = _metrics(group)
                for metric in requested_metrics:
                    item[_public_metric(metric)] = _round_metric(metric, values[metric])
                item["attainment_rate"] = _rounded(values["attainment_rate"], 4)
                item["row_count"] = len(group)
                result_rows.append(item)
            sort_field = _sort_field(sort_by) or "revenue_cny"
            reverse = str(sort_by or "").startswith("-") or not sort_by
            result_rows.sort(key=lambda item: (_numeric(item.get(sort_field)), str(item)), reverse=reverse)
            if limit:
                result_rows = result_rows[: max(1, min(int(limit), 100))]
        else:
            result_rows = [
                {
                    **{_public_metric(metric): _round_metric(metric, totals[metric]) for metric in requested_metrics},
                    "attainment_rate": _rounded(totals["attainment_rate"], 4),
                    "row_count": len(rows),
                }
            ]
        citation = self._citation()
        period = _period(rows)
        summary = (
            f"销售查询覆盖 {len(rows)} 条事实记录（{period}），销售额 {_money(totals['amount'])}，"
            f"目标 {_money(totals['target'])}，达成率 {_percent(totals['attainment_rate'])}。 {citation}"
        )
        return ToolResult(
            name="query_sales",
            data={
                "filters": {key: value for key, value in filters.items() if value},
                "group_by": dimensions,
                "rows": result_rows,
                "totals": _public_metrics(totals),
                "period": period,
            },
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=bool(dimensions)),
            confidence=0.99,
        )

    def query_feedback(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        regions: Optional[Sequence[str]] = None,
        product_ids: Optional[Sequence[str]] = None,
        topics: Optional[Sequence[str]] = None,
        sentiments: Optional[Sequence[str]] = None,
        urgencies: Optional[Sequence[str]] = None,
        limit: int = 20,
        **_: Any,
    ) -> ToolResult:
        table = self._feedback_table()
        if not table:
            return ToolResult(
                name="query_feedback",
                data={"rows": []},
                summary="没有可用的一线反馈数据。",
                confidence=0.0,
                warnings=["frontline feedback CSV not found"],
            )
        rows = [dict(row) for row in table.rows]
        fields = table.fieldnames
        date_col = _find_column(fields, ("feedback_date", "date", "日期"))
        region_col = _find_column(fields, ALIASES["region"])
        product_col = _find_column(fields, ALIASES["product_id"])
        topic_col = _find_column(fields, ("topic", "主题", "问题类型"))
        sentiment_col = _find_column(fields, ("sentiment", "情绪", "倾向"))
        urgency_col = _find_column(fields, ("urgency", "紧急度", "优先级"))
        start, end = parse_date(date_from), parse_date(date_to)
        if start and date_col:
            rows = [row for row in rows if parse_date(row.get(date_col)) and parse_date(row.get(date_col)) >= start]
        if end and date_col:
            rows = [row for row in rows if parse_date(row.get(date_col)) and parse_date(row.get(date_col)) <= end]
        for wanted_values, column in (
            (regions, region_col),
            (product_ids, product_col),
            (topics, topic_col),
            (sentiments, sentiment_col),
            (urgencies, urgency_col),
        ):
            if wanted_values and column:
                if isinstance(wanted_values, str):
                    wanted_values = [wanted_values]
                wanted = {str(item).casefold() for item in wanted_values}
                rows = [row for row in rows if str(row.get(column, "")).casefold() in wanted]
        topics_count = Counter(row.get(topic_col, "未分类") or "未分类" for row in rows) if topic_col else Counter()
        sentiment_count = Counter(row.get(sentiment_col, "未标注") or "未标注" for row in rows) if sentiment_col else Counter()
        urgency_count = Counter(row.get(urgency_col, "未标注") or "未标注" for row in rows) if urgency_col else Counter()
        safe_limit = max(1, min(int(limit or 20), 100))
        public_rows = rows[:safe_limit]
        citation = f"[DATA:{table.name}]"
        top_topic = topics_count.most_common(1)[0] if topics_count else ("无", 0)
        filter_parts: List[str] = []
        if regions:
            filter_parts.append("区域=" + "、".join(str(item) for item in regions))
        if sentiments:
            filter_parts.append("倾向=" + "、".join(str(item) for item in sentiments))
        if urgencies:
            filter_parts.append("紧急度=" + "、".join(str(item) for item in urgencies))
        if topics:
            filter_parts.append("主题=" + "、".join(str(item) for item in topics))
        filter_text = f"筛选条件：{'；'.join(filter_parts)}。" if filter_parts else ""
        summary = (
            f"{filter_text}筛选到 {len(rows)} 条一线反馈；"
            f"最高频主题为“{top_topic[0]}”（{top_topic[1]} 条）。 {citation}"
        )
        source = Source(
            id=f"DATA:{table.name}",
            type="structured_data",
            file=f"data/{table.name}",
            citation=citation,
            description="按条件筛选的一线业务反馈",
            rows_used=len(rows),
        )
        return ToolResult(
            name="query_feedback",
            data={
                "rows": public_rows,
                "matched_count": len(rows),
                "topic_counts": dict(topics_count.most_common()),
                "sentiment_counts": dict(sentiment_count.most_common()),
                "urgency_counts": dict(urgency_count.most_common()),
            },
            summary=summary,
            sources=[source],
            confidence=0.98 if rows else 0.45,
            warnings=[] if rows else ["筛选条件下没有反馈记录"],
        )

    def national_sales_summary(self, **params: Any) -> ToolResult:
        rows = self._rows_from_params(params)
        if not rows:
            return self._empty("national_sales_summary", params)
        values = _metrics(rows)
        latest, previous, growth = _latest_growth(rows)
        regions = {row["region"] for row in rows if row.get("region")}
        products = {row["product"] for row in rows if row.get("product")}
        institutions = {row["institution"] for row in rows if row.get("institution")}
        query = str(params.get("_query") or "")
        scope_parts = list(params.get("regions") or [])
        product_ids = sorted({row.get("product_id", "") for row in rows if row.get("product_id")})
        if len(product_ids) == 1:
            scope_parts.append(product_ids[0])
        scope = "、".join(scope_parts) or "全国"
        citation = self._citation()
        growth_text = "暂无足够周次计算环比"
        if growth is not None:
            growth_text = f"最新周销售额 {_money(latest)}，较上一周 {growth:+.1%}"
        period_label = "最近四周" if int(params.get("relative_weeks") or 0) == 4 else _period(rows)
        summary = (
            f"### {scope}销售摘要（{period_label}）\n"
            f"- 销售额 **{_money(values['amount'])}**，目标 {_money(values['target'])}，"
            f"达成率 **{_percent(values['attainment_rate'])}**（比例 {values['attainment_rate']:.4f}）；"
            f"净签约套数/销量 {_number(values['units'])}。 {citation}\n"
            f"- 覆盖 {len(regions)} 个区域、{len(products)} 个产品、{len(institutions)} 家机构；{growth_text}。 {citation}"
        )
        if any(cue in query for cue in ("试用", "转签", "转化")):
            summary += (
                f"\n- 试用 {_number(values['trials'])} 条，转签 {_number(values['conversions'])} 条，"
                f"试用转签率 {values['conversion_rate']:.4f}（{_percent(values['conversion_rate'])}）。 {citation}"
            )
        if any(cue in query for cue in ("退订", "退货")):
            summary += f"\n- 退订/退货记录 {_number(values['returns'])} 条。 {citation}"
        if "第一周" in query and ("最后一周" in query or "末周" in query):
            weekly: Dict[str, float] = defaultdict(float)
            for row in rows:
                weekly[str(row.get("date", ""))] += _numeric(row.get("amount"))
            ordered = sorted(key for key in weekly if key)
            if len(ordered) >= 2:
                first_value, last_value = weekly[ordered[0]], weekly[ordered[-1]]
                overall_growth = (last_value - first_value) / first_value if first_value else 0.0
                summary += (
                    f"\n- 第一周 {ordered[0]} 销售额 {_money(first_value)}；最后一周 {ordered[-1]} 销售额 "
                    f"{_money(last_value)}；增长率 {overall_growth:.4f}（{overall_growth:.1%}）。 {citation}"
                )
        return ToolResult(
            name="national_sales_summary",
            data={
                "period": _period(rows),
                "metrics": _public_metrics(values),
                "coverage": {
                    "regions": len(regions),
                    "products": len(products),
                    "institutions": len(institutions),
                    "rows": len(rows),
                },
                "latest_week_revenue_cny": _rounded(latest, 2),
                "previous_week_revenue_cny": _rounded(previous, 2),
                "week_over_week_growth": _rounded(growth, 4) if growth is not None else None,
            },
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=True),
            confidence=0.99,
        )

    def region_comparison(self, **params: Any) -> ToolResult:
        rows = self._rows_from_params(params)
        if not rows:
            return self._empty("region_comparison", params)
        ranked = _group_metrics(rows, "region")
        citation = self._citation()
        lines = ["| 区域 | 销售额 | 目标达成率 | 销量 | 最新周环比 |", "|---|---:|---:|---:|---:|"]
        for item in ranked:
            _, _, growth = _latest_growth(item.pop("_rows"))
            item["week_over_week_growth"] = growth
            lines.append(
                f"| {item['name'] or '未标注'} | {_money(item['amount'])} | "
                f"{_percent(item['attainment_rate'])}（{item['attainment_rate']:.4f}） | {_number(item['units'])} | {_percent(growth, signed=True)} |"
            )
        leader = ranked[0]
        best_attainment = max(ranked, key=lambda item: item["attainment_rate"])
        weakest_attainment = min(ranked, key=lambda item: item["attainment_rate"])
        query_text = str(params.get("_query") or "")
        trend_finding = ""
        if any(cue in query_text for cue in ("下降", "下滑", "增长")):
            comparable = [item for item in ranked if item.get("week_over_week_growth") is not None]
            declining = [item for item in comparable if item["week_over_week_growth"] < 0]
            if declining:
                steepest = min(declining, key=lambda item: item["week_over_week_growth"])
                trend_finding = (
                    f"最新周下降最多的是 **{steepest['name']}**"
                    f"（{_percent(steepest['week_over_week_growth'], signed=True)}）。 "
                )
            elif comparable:
                slowest = min(comparable, key=lambda item: item["week_over_week_growth"])
                trend_finding = (
                    "最新周各区域均未下降；"
                    f"增幅最小的是 **{slowest['name']}**"
                    f"（{_percent(slowest['week_over_week_growth'], signed=True)}）。 "
                )
        summary = (
            f"### 区域对比（{_period(rows)}）\n" + "\n".join(lines) + "\n\n"
            + trend_finding
            + f"销售额领先区域是 **{leader['name']}**（{_money(leader['amount'])}）；"
            f"目标达成率最高是 **{best_attainment['name']}**（{best_attainment['attainment_rate']:.4f}），"
            f"最弱是 **{weakest_attainment['name']}**（{weakest_attainment['attainment_rate']:.4f}）。 {citation}"
        )
        return ToolResult(
            name="region_comparison",
            data={"period": _period(rows), "regions": [_clean_group(item) for item in ranked]},
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=True),
            confidence=0.99,
        )

    def product_performance(self, **params: Any) -> ToolResult:
        rows = self._rows_from_params(params)
        if not rows:
            return self._empty("product_performance", params)
        ranked = _group_metrics(rows, "product")
        scoped_institutions = sorted({str(row.get("institution", "")) for row in rows if row.get("institution")})
        institution_scope = (
            scoped_institutions[0]
            if len(scoped_institutions) == 1 and (params.get("institutions") or params.get("org_ids"))
            else ""
        )
        total = sum(item["amount"] for item in ranked)
        citation = self._citation()
        lines = ["| 产品 | 销售额 | 销售占比 | 达成率 | 销量 |", "|---|---:|---:|---:|---:|"]
        for item in ranked:
            group_rows = item["_rows"]
            item["product_id"] = str(group_rows[0].get("product_id", "")) if group_rows else ""
            item["revenue_share"] = item["amount"] / total if total else 0.0
            lines.append(
                f"| {item['product_id']} · {item['name'] or '未标注'} | {_money(item['amount'])} | {_percent(item['revenue_share'])} | "
                f"{_percent(item['attainment_rate'])}（{item['attainment_rate']:.4f}） | {_number(item['units'])} |"
            )
        summary = (
            f"### 产品表现{'｜' + institution_scope if institution_scope else ''}（{_period(rows)}）\n"
            + "\n".join(lines)
            + "\n\n"
            f"销售额最高的产品是 **{ranked[0]['name']}**，占筛选范围销售额的 "
            f"{_percent(ranked[0]['revenue_share'])}。平均折扣率：" + "；".join(
                f"{item['product_id']} {item['average_discount_rate']:.4f}" for item in ranked
            )
            + (f"。筛选机构：**{institution_scope}**" if institution_scope else "")
            + f"。 {citation}"
        )
        return ToolResult(
            name="product_performance",
            data={"period": _period(rows), "products": [_clean_group(item) for item in ranked]},
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=True),
            confidence=0.99,
        )

    def top_institutions(self, **params: Any) -> ToolResult:
        rows = self._rows_from_params(params)
        if not rows:
            return self._empty("top_institutions", params)
        top_k = max(1, min(int(params.get("top_k") or 5), 50))
        query = str(params.get("_query") or "")
        lowest_first = any(cue in query for cue in ("最低", "最少", "最差"))
        ranked_all = _group_metrics(rows, "institution")
        if lowest_first:
            ranked_all.sort(key=lambda item: (item["amount"], item["name"]))
        ranked = ranked_all[:top_k]
        for item in ranked:
            group_rows = item["_rows"]
            item["region"] = Counter(row.get("region", "") for row in group_rows).most_common(1)[0][0]
        citation = self._citation()
        lines = ["| 排名 | 机构 | 区域 | 销售额 | 达成率 |", "|---:|---|---|---:|---:|"]
        for index, item in enumerate(ranked, 1):
            lines.append(
                f"| {index} | {item['name'] or '未标注'} | {item['region'] or '未标注'} | "
                f"{_money(item['amount'])} | {_percent(item['attainment_rate'])}（{item['attainment_rate']:.4f}） |"
            )
        heading = f"销售额最低 {len(ranked)} 家机构" if lowest_first else f"Top {len(ranked)} 机构"
        summary = f"### {heading}（{_period(rows)}）\n" + "\n".join(lines) + f"\n\n{citation}"
        return ToolResult(
            name="top_institutions",
            data={"period": _period(rows), "institutions": [_clean_group(item) for item in ranked]},
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=True),
            confidence=0.99,
        )

    def institution_history(self, **params: Any) -> ToolResult:
        rows = self._rows_from_params(params)
        selected = params.get("institutions") or []
        if not rows:
            return self._empty("institution_history", params)
        if not selected:
            # A safe, deterministic fallback for a vague "机构趋势" question.
            top = _group_metrics(rows, "institution")
            if top:
                chosen = top[0]["name"]
                rows = [row for row in rows if row.get("institution") == chosen]
                selected = [chosen]
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row.get("institution", "")), str(row.get("date", "")))].append(row)
        history: List[Dict[str, Any]] = []
        for (institution, week), group in sorted(grouped.items(), key=lambda item: item[0]):
            values = _metrics(group)
            history.append({"institution": institution, "week_start": week, **_public_metrics(values)})
        citation = self._citation()
        lines = ["| 周次 | 机构 | 销售额 | 达成率 |", "|---|---|---:|---:|"]
        for item in history[-16:]:
            lines.append(
                f"| {item['week_start']} | {item['institution']} | {_money(item['revenue_cny'])} | "
                f"{_percent(item['attainment_rate'])} |"
            )
        _, _, growth = _latest_growth(rows)
        summary = (
            f"### 机构历史：{'、'.join(selected)}\n" + "\n".join(lines) + "\n\n"
            f"最新周较上一周变化 {_percent(growth, signed=True)}。 {citation}"
        )
        return ToolResult(
            name="institution_history",
            data={"institutions": list(selected), "history": history, "week_over_week_growth": growth},
            summary=summary,
            sources=self.catalog.data_source(len(rows), include_dimensions=True),
            confidence=0.98,
            warnings=[] if params.get("institutions") else ["未指定机构，默认展示累计销售额最高机构"],
        )

    def region_diagnosis(self, **params: Any) -> ToolResult:
        all_rows = self._rows_from_params({key: value for key, value in params.items() if key != "regions"})
        if not all_rows:
            return self._empty("region_diagnosis", params)
        requested = list(params.get("regions") or [])
        region_groups = _group_metrics(all_rows, "region")
        if not requested:
            target_region = min(region_groups, key=lambda item: (item["attainment_rate"], item["amount"]))["name"]
            requested = [target_region]
        region_rows = [row for row in all_rows if row.get("region") in set(requested)]
        if not region_rows:
            return self._empty("region_diagnosis", params)
        regional = _metrics(region_rows)
        national = _metrics(all_rows)
        _, _, growth = _latest_growth(region_rows)
        institution_groups = _group_metrics(region_rows, "institution")
        product_groups = _group_metrics(region_rows, "product")
        top3_share = sum(item["amount"] for item in institution_groups[:3]) / regional["amount"] if regional["amount"] else 0.0
        conversion_rate = regional["conversions"] / regional["trials"] if regional["trials"] else 0.0
        national_conversion = national["conversions"] / national["trials"] if national["trials"] else 0.0
        reasons: List[Dict[str, Any]] = []
        if regional["attainment_rate"] + 0.005 < national["attainment_rate"]:
            reasons.append(
                {
                    "factor": "目标达成",
                    "finding": f"区域达成率 {_percent(regional['attainment_rate'])}，低于整体 {_percent(national['attainment_rate'])}",
                    "severity": "high",
                }
            )
        if growth is not None and growth < 0:
            reasons.append(
                {"factor": "近期趋势", "finding": f"最新周销售额环比 {growth:+.1%}", "severity": "high"}
            )
        if regional["trials"] and conversion_rate + 0.01 < national_conversion:
            reasons.append(
                {
                    "factor": "试用转化",
                    "finding": f"试用转化率 {_percent(conversion_rate)}，低于整体 {_percent(national_conversion)}",
                    "severity": "medium",
                }
            )
        if top3_share > 0.65:
            reasons.append(
                {
                    "factor": "机构集中度",
                    "finding": f"Top 3 机构贡献 {_percent(top3_share)}，收入集中度较高",
                    "severity": "medium",
                }
            )
        if not reasons:
            reasons.append(
                {
                    "factor": "经营基线",
                    "finding": "结构化指标未显示显著异常，建议结合一线反馈继续核查",
                    "severity": "low",
                }
            )
        feedback = self.query_feedback(regions=requested, limit=5)
        citation = self._citation()
        lines = [f"- **{item['factor']}**：{item['finding']}。 {citation}" for item in reasons]
        if feedback.data.get("matched_count"):
            counts = feedback.data.get("topic_counts", {})
            topic_text = "、".join(f"{key}（{value}）" for key, value in list(counts.items())[:3])
            lines.append(f"- **一线信号**：高频反馈主题为 {topic_text}。 {feedback.sources[0].citation}")
        best_product = product_groups[0]["name"] if product_groups else "无"
        summary = (
            f"### {'、'.join(requested)}区域原因分析（{_period(region_rows)}）\n"
            f"区域销售额 {_money(regional['amount'])}，达成率 {_percent(regional['attainment_rate'])}"
            f"（比例 {regional['attainment_rate']:.4f}）；"
            f"贡献最高产品为 {best_product}。 {citation}\n\n" + "\n".join(lines) +
            "\n\n以上是相关性诊断，不把聚合数据解读为已证实的因果关系。"
        )
        sources = _merge_sources(self.catalog.data_source(len(region_rows), include_dimensions=True), feedback.sources)
        return ToolResult(
            name="region_diagnosis",
            data={
                "regions": requested,
                "period": _period(region_rows),
                "metrics": _public_metrics(regional),
                "benchmarks": {"national_attainment_rate": national["attainment_rate"], "national_conversion_rate": national_conversion},
                "week_over_week_growth": growth,
                "top3_institution_share": top3_share,
                "reasons": reasons,
                "feedback": feedback.data,
            },
            summary=summary,
            sources=sources,
            confidence=0.91,
            warnings=["原因分析为指标相关性假设，并非因果证明"],
        )

    def weekly_report(self, **params: Any) -> ToolResult:
        explicit_period = params.get("start_date") or params.get("end_date") or params.get("relative_weeks")
        report_params = dict(params)
        if not explicit_period:
            report_params["relative_weeks"] = 1
        rows = self._rows_from_params(report_params)
        if not rows:
            return self._empty("weekly_report", report_params)
        values = _metrics(rows)
        regions = _group_metrics(rows, "region")
        products = _group_metrics(rows, "product")
        institutions = _group_metrics(rows, "institution")[:5]
        all_for_trend = self._rows_from_params({key: value for key, value in params.items() if key not in {"start_date", "end_date", "relative_weeks"}})
        _, _, growth = _latest_growth(all_for_trend)
        feedback = self.query_feedback(
            date_from=min((row.get("date") for row in rows if row.get("date")), default=None),
            date_to=max((row.get("date_end") or row.get("date") for row in rows if row.get("date")), default=None),
            regions=params.get("regions"),
            limit=5,
        )
        citation = self._citation()
        risk_regions = [item for item in regions if item["attainment_rate"] < 1.0]
        actions: List[str] = []
        if risk_regions:
            actions.append(f"优先复盘 {'、'.join(item['name'] for item in risk_regions[:2])} 的目标缺口")
        if growth is not None and growth < 0:
            actions.append("按产品与重点机构拆解最新周环比下滑")
        if values["trials"] and values["conversions"] / values["trials"] < 0.5:
            actions.append("检查试用到成交阶段的跟进节奏")
        if not actions:
            actions.append("保持当前节奏，并跟踪低于全国达成率的区域")
        lines = [
            f"### NovaMed 经营周报（{_period(rows)}）",
            f"**核心业绩**：销售额 {_money(values['amount'])}，目标达成率 {_percent(values['attainment_rate'])}，"
            f"销量 {_number(values['units'])}；最新周环比 {_percent(growth, signed=True)}。 {citation}",
            "",
            f"**区域**：{_compact_ranking(regions, 4)}。 {citation}",
            f"**产品**：{_compact_ranking(products, 3)}。 {citation}",
            f"**Top 机构**：{_compact_ranking(institutions, 5)}。 {citation}",
        ]
        if feedback.data.get("matched_count"):
            topic_text = "、".join(
                f"{key} {value} 条" for key, value in list(feedback.data.get("topic_counts", {}).items())[:3]
            )
            lines.extend(["", f"**一线反馈**：{topic_text}。 {feedback.sources[0].citation}"])
        lines.extend(["", "**下周建议**：" + "；".join(actions) + "。"])
        sources = _merge_sources(self.catalog.data_source(len(rows), include_dimensions=True), feedback.sources)
        return ToolResult(
            name="weekly_report",
            data={
                "period": _period(rows),
                "metrics": _public_metrics(values),
                "week_over_week_growth": growth,
                "regions": [_clean_group(item) for item in regions],
                "products": [_clean_group(item) for item in products],
                "top_institutions": [_clean_group(item) for item in institutions],
                "feedback": feedback.data,
                "recommended_actions": actions,
            },
            summary="\n".join(lines),
            sources=sources,
            confidence=0.96,
        )

    def business_analysis(self, **params: Any) -> ToolResult:
        """Broad evidence pack for cross-dimensional management questions."""

        query = str(params.get("_query") or "")
        rows = self._rows_from_params(params)
        if not rows:
            return self._empty("business_analysis", params)
        total = _metrics(rows)
        regions = _group_metrics(rows, "region")
        products = _group_metrics(rows, "product")
        institutions = _group_metrics(rows, "institution")
        channels = _group_metrics(rows, "channel")
        segments = _group_metrics(rows, "target_segment")
        weeks: Dict[str, float] = defaultdict(float)
        for row in rows:
            weeks[str(row.get("date", ""))] += _numeric(row.get("amount"))
        week_keys = sorted(key for key in weeks if key)
        first = weeks[week_keys[0]] if week_keys else 0.0
        last = weeks[week_keys[-1]] if week_keys else 0.0
        first_last_growth = (last - first) / first if first else 0.0
        first_two = sum(weeks[key] for key in week_keys[:2])
        last_two = sum(weeks[key] for key in week_keys[-2:])
        two_period_growth = (last_two - first_two) / first_two if first_two else 0.0
        sales_citation = self._citation()

        region_parts = [
            f"{item['name']} 销售额 {_money(item['amount'])}、达成率 {item['attainment_rate']:.4f}、"
            f"目标缺口 {_money(max(item['target'] - item['amount'], 0))}"
            for item in sorted(regions, key=lambda item: -item["attainment_rate"])
        ]
        product_parts: List[str] = []
        for item in products:
            group = item["_rows"]
            product_id = str(group[0].get("product_id", "")) if group else ""
            best_region = _group_metrics(group, "region")[0]["name"] if group else ""
            product_parts.append(
                f"{product_id} {item['name']}：销售额 {_money(item['amount'])}、达成率 {item['attainment_rate']:.4f}、"
                f"平均折扣 {item['average_discount_rate']:.4f}、销售额最高区域 {best_region}"
            )
        institution_parts = [
            f"{item['name']} {_money(item['amount'])}（达成率 {item['attainment_rate']:.4f}）"
            for item in institutions[:3]
        ]
        if institutions:
            institution_parts.append(
                f"最低机构 {institutions[-1]['name']} {_money(institutions[-1]['amount'])}"
            )
        channel_parts = [f"{item['name']} {_money(item['amount'])}" for item in channels]
        segment_parts = [f"{item['name']}类 {item['attainment_rate']:.4f}" for item in segments if item["name"]]

        feedback = self.query_feedback(
            regions=params.get("regions"),
            product_ids=params.get("product_ids"),
            limit=100,
        )
        feedback_rows = feedback.data.get("rows", [])
        negative_topics = Counter(
            row.get("topic", "") for row in feedback_rows if row.get("sentiment") == "负向"
        )
        urgent_rows = [row for row in feedback_rows if row.get("urgency") == "紧急"]
        urgent_regions = Counter(row.get("region", "") for row in urgent_rows)
        excerpts = "；".join(
            str(row.get("feedback_text", "")) for row in feedback_rows if row.get("feedback_text")
        )
        if len(excerpts) > 700:
            excerpts = excerpts[:699] + "…"
        feedback_citation = feedback.sources[0].citation if feedback.sources else ""
        top_negative = negative_topics.most_common(1)[0] if negative_topics else ("无", 0)
        urgent_region = urgent_regions.most_common(1)[0][0] if urgent_regions else "无"

        summary = (
            f"### 综合经营分析（{_period(rows)}）\n"
            f"- **全国业绩**：销售额 {_money(total['amount'])}，目标 {_money(total['target'])}，"
            f"目标达成率 {total['attainment_rate']:.4f}（{_percent(total['attainment_rate'])}）。 {sales_citation}\n"
            f"- **趋势**：第一周销售额 {_money(first)}，最后一周 {_money(last)}，增长率 {first_last_growth:.4f}；"
            f"最初两周 {_money(first_two)}，最后两周 {_money(last_two)}，两阶段增长率 {two_period_growth:.4f}。 {sales_citation}\n"
            f"- **区域优先级**：{'；'.join(region_parts)}。 {sales_citation}\n"
            f"- **产品**：{'；'.join(product_parts)}。 {sales_citation}\n"
            f"- **机构**：{'；'.join(institution_parts)}。 {sales_citation}\n"
            f"- **渠道**：{'；'.join(channel_parts)}。 {sales_citation}\n"
            f"- **机构分层**：{'；'.join(segment_parts)}。 {sales_citation}\n"
            f"- **销售漏斗**：拜访 {_number(total['visits'])}、演示 {_number(total['demos'])}、试用 "
            f"{_number(total['trials'])}、转签 {_number(total['conversions'])}；演示率 {total['demo_rate']:.4f}，"
            f"试用转签率 {total['conversion_rate']:.4f}；退订 {_number(total['returns'])}。 {sales_citation}\n"
            f"- **客户反馈/声音**：正向 {feedback.data.get('sentiment_counts', {}).get('正向', 0)} 条，"
            f"负向 {feedback.data.get('sentiment_counts', {}).get('负向', 0)} 条；紧急 {len(urgent_rows)} 条，"
            f"最集中区域 {urgent_region}；最高频负向主题 {top_negative[0]}（{top_negative[1]} 条）。 {feedback_citation}\n"
            f"- **一线信号**：{excerpts or '暂无可用反馈文本'} {feedback_citation}\n\n"
            "建议围绕目标缺口安排复盘，把接口/PACS与部署问题交给售前和工程协同，补足培训，并为每个风险写明责任人、截止日期和行动项。"
        )
        sources = _merge_sources(self.catalog.data_source(len(rows), include_dimensions=True), feedback.sources)
        return ToolResult(
            name="business_analysis",
            data={
                "query": query,
                "metrics": _public_metrics(total),
                "first_week_revenue_cny": first,
                "last_week_revenue_cny": last,
                "first_last_growth_rate": _rounded(first_last_growth, 4),
                "first_two_weeks_revenue_cny": first_two,
                "last_two_weeks_revenue_cny": last_two,
                "two_period_growth_rate": _rounded(two_period_growth, 4),
                "regions": [_clean_group(item) for item in regions],
                "products": [_clean_group(item) for item in products],
                "institutions": [_clean_group(item) for item in institutions],
                "channels": [_clean_group(item) for item in channels],
                "segments": [_clean_group(item) for item in segments],
                "feedback": feedback.data,
            },
            summary=summary,
            sources=sources,
            confidence=0.96,
        )

    def _rows_from_params(self, params: Mapping[str, Any]) -> List[Dict[str, Any]]:
        filters = {
            "regions": params.get("regions"),
            "products": params.get("products"),
            "institutions": params.get("institutions"),
            "provinces": params.get("provinces"),
            "cities": params.get("cities"),
            "tiers": params.get("tiers"),
            "start_date": params.get("start_date") or params.get("date_from"),
            "end_date": params.get("end_date") or params.get("date_to"),
            "relative_weeks": params.get("relative_weeks"),
        }
        rows = self.catalog.filter_sales(filters)
        for key, field in (("product_ids", "product_id"), ("org_ids", "institution_id")):
            wanted_values = params.get(key) or []
            if wanted_values:
                if isinstance(wanted_values, str):
                    wanted_values = [wanted_values]
                wanted = {str(item).casefold() for item in wanted_values}
                rows = [row for row in rows if str(row.get(field, "")).casefold() in wanted]
        return rows

    def _feedback_table(self) -> Optional[CSVTable]:
        candidates: List[Tuple[int, CSVTable]] = []
        for table in self.catalog.tables.values():
            fields = {_normalize_field(field) for field in table.fieldnames}
            score = sum(item in fields for item in ("feedbacktext", "sentiment", "topic", "urgency"))
            if "feedback" in table.name.casefold() or "反馈" in table.name:
                score += 3
            if score:
                candidates.append((score, table))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def _empty(self, name: str, filters: Mapping[str, Any]) -> ToolResult:
        data_status = self.catalog.status()
        warning = "当前筛选条件下没有销售记录。"
        if not self.catalog.sales:
            warning = f"未在 {self.catalog.data_dir} 找到可用的销售 CSV。"
        return ToolResult(
            name=name,
            data={"filters": {key: value for key, value in filters.items() if value}, "data_status": data_status},
            summary="没有足够的结构化数据支持该回答。",
            sources=[],
            confidence=0.0,
            warnings=[warning],
        )

    def _citation(self) -> str:
        return f"[DATA:{self.catalog.sales_file}]" if self.catalog.sales_file else "[DATA:unavailable]"

    @staticmethod
    def openai_schemas() -> List[Dict[str, Any]]:
        """Responses API function definitions (strict allow-list, read-only)."""

        return [
            {
                "type": "function",
                "name": "query_sales",
                "description": "查询并聚合 NovaMed 销售数据；只用于结构化经营指标。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                        "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                        "regions": {"type": "array", "items": {"type": "string"}},
                        "product_ids": {"type": "array", "items": {"type": "string"}},
                        "org_ids": {"type": "array", "items": {"type": "string"}},
                        "metrics": {"type": "array", "items": {"type": "string"}},
                        "group_by": {"type": "array", "items": {"type": "string"}},
                        "sort_by": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "query_feedback",
                "description": "查询一线反馈，用于解释业务信号但不能证明因果。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date_from": {"type": "string"},
                        "date_to": {"type": "string"},
                        "regions": {"type": "array", "items": {"type": "string"}},
                        "product_ids": {"type": "array", "items": {"type": "string"}},
                        "topics": {"type": "array", "items": {"type": "string"}},
                        "sentiments": {"type": "array", "items": {"type": "string"}},
                        "urgencies": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
            },
        ]


def _metrics(rows: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    materialized = list(rows)
    values = {field: 0.0 for field in ("amount", "target", "units", "visits", "demos", "trials", "conversions", "returns", "discount_rate")}
    for row in materialized:
        for field in values:
            values[field] += _numeric(row.get(field))
    values["attainment_rate"] = values["amount"] / values["target"] if values["target"] else 0.0
    values["average_unit_revenue"] = values["amount"] / values["units"] if values["units"] else 0.0
    values["conversion_rate"] = values["conversions"] / values["trials"] if values["trials"] else 0.0
    values["demo_rate"] = values["demos"] / values["visits"] if values["visits"] else 0.0
    values["return_rate"] = values["returns"] / values["units"] if values["units"] else 0.0
    values["average_discount_rate"] = values["discount_rate"] / len(materialized) if materialized else 0.0
    return values


def _public_metrics(values: Mapping[str, float]) -> Dict[str, float]:
    return {
        "revenue_cny": _rounded(values.get("amount"), 2),
        "target_revenue_cny": _rounded(values.get("target"), 2),
        "attainment_rate": _rounded(values.get("attainment_rate"), 4),
        "units": _rounded(values.get("units"), 2),
        "visits": _rounded(values.get("visits"), 2),
        "demos": _rounded(values.get("demos"), 2),
        "trials": _rounded(values.get("trials"), 2),
        "conversions": _rounded(values.get("conversions"), 2),
        "returns": _rounded(values.get("returns"), 2),
        "conversion_rate": _rounded(values.get("conversion_rate"), 4),
        "demo_rate": _rounded(values.get("demo_rate"), 4),
        "return_rate": _rounded(values.get("return_rate"), 4),
        "average_discount_rate": _rounded(values.get("average_discount_rate"), 4),
        "average_unit_revenue_cny": _rounded(values.get("average_unit_revenue"), 2),
    }


def _group_metrics(rows: Iterable[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field, ""))].append(row)
    result: List[Dict[str, Any]] = []
    for name, group in groups.items():
        result.append({"name": name, **_metrics(group), "row_count": len(group), "_rows": group})
    result.sort(key=lambda item: (-item["amount"], item["name"]))
    return result


def _latest_growth(rows: Iterable[Mapping[str, Any]]) -> Tuple[float, float, Optional[float]]:
    weeks: Dict[str, float] = defaultdict(float)
    for row in rows:
        if row.get("date"):
            weeks[str(row["date"])] += _numeric(row.get("amount"))
    ordered = sorted(weeks)
    if not ordered:
        return 0.0, 0.0, None
    latest = weeks[ordered[-1]]
    if len(ordered) < 2:
        return latest, 0.0, None
    previous = weeks[ordered[-2]]
    growth = (latest - previous) / previous if previous else None
    return latest, previous, growth


def _period(rows: Iterable[Mapping[str, Any]]) -> str:
    dates: List[date] = []
    end_dates: List[date] = []
    for row in rows:
        parsed = row.get("_date") if isinstance(row.get("_date"), date) else parse_date(row.get("date"))
        if parsed:
            dates.append(parsed)
        parsed_end = parse_date(row.get("date_end"))
        if parsed_end:
            end_dates.append(parsed_end)
    if not dates:
        return "全量数据"
    start = min(dates).isoformat()
    end = max(end_dates or dates).isoformat()
    return start if start == end else f"{start} 至 {end}"


def _clean_group(item: Mapping[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"name": item.get("name", "")}
    for key in (
        "amount",
        "target",
        "attainment_rate",
        "units",
        "visits",
        "demos",
        "trials",
        "conversions",
        "returns",
        "conversion_rate",
        "return_rate",
        "average_unit_revenue",
        "row_count",
        "week_over_week_growth",
        "revenue_share",
        "region",
    ):
        if key in item:
            public_key = _public_metric(key) if key in {"amount", "target", "average_unit_revenue"} else key
            result[public_key] = _rounded(item[key], 4) if isinstance(item[key], float) else item[key]
    return result


def _compact_ranking(items: Sequence[Mapping[str, Any]], limit: int) -> str:
    return "；".join(
        f"{index}. {item.get('name') or '未标注'} {_money(_numeric(item.get('amount')))}"
        for index, item in enumerate(items[:limit], 1)
    ) or "无数据"


def _merge_sources(*groups: Sequence[Source]) -> List[Source]:
    merged: List[Source] = []
    seen = set()
    for group in groups:
        for source in group:
            if source.id not in seen:
                seen.add(source.id)
                merged.append(source)
    return merged


def _normalize_field(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value).casefold())


def _numeric(value: Any) -> float:
    return parse_number(value)


def _rounded(value: Any, digits: int = 2) -> float:
    return round(_numeric(value), digits)


def _money(value: Any) -> str:
    return f"¥{_numeric(value):,.0f}"


def _number(value: Any) -> str:
    number = _numeric(value)
    return f"{number:,.0f}" if float(number).is_integer() else f"{number:,.2f}"


def _percent(value: Optional[float], *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.1%}" if signed else f"{value:.1%}"


def _public_metric(metric: str) -> str:
    return {"amount": "revenue_cny", "target": "target_revenue_cny", "average_unit_revenue": "average_unit_revenue_cny"}.get(metric, metric)


def _round_metric(metric: str, value: float) -> float:
    return _rounded(value, 4 if metric.endswith("rate") else 2)


def _sort_field(sort_by: Optional[str]) -> str:
    field = str(sort_by or "").lstrip("+-").casefold()
    mapped = METRIC_FIELDS.get(field, field)
    return _public_metric(mapped)
