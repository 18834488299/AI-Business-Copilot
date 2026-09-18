#!/usr/bin/env python3
"""Validate NovaMed demo data, knowledge files, and benchmark invariants."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = ROOT / "knowledge_base"
BENCHMARK_DIR = ROOT / "benchmark"

EXPECTED_HEADERS = {
    "institutions.csv": ["org_id", "org_name", "region", "province", "city", "institution_type", "tier", "bed_count", "account_owner", "target_segment"],
    "products.csv": ["product_id", "product_name", "category", "list_price_cny", "launch_date", "status"],
    "sales_weekly.csv": ["week_start", "week_end", "region", "org_id", "product_id", "units", "list_price_cny", "discount_rate", "unit_price_cny", "revenue_cny", "target_revenue_cny", "visits", "demos", "trials", "conversions", "returns", "channel"],
    "frontline_feedback.csv": ["feedback_id", "feedback_date", "region", "org_id", "product_id", "submitter_role", "sentiment", "topic", "urgency", "feedback_text", "followup_status"],
}
EXPECTED_CATEGORY_COUNTS = {"Knowledge/RAG": 25, "数据查询": 30, "Tool参数": 20, "综合分析": 25, "无答案/异常": 10, "多轮": 10}


def read_csv(name: str) -> tuple[list[str], list[dict[str, str]]]:
    path = DATA_DIR / name
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_benchmark_schema(case: dict[str, Any]) -> None:
    require(set(case) == {"id", "category", "input", "expected", "metadata"}, f"unexpected top-level fields in {case.get('id')}")
    require(isinstance(case["input"].get("messages"), list) and case["input"]["messages"], f"missing messages in {case['id']}")
    for message in case["input"]["messages"]:
        require(set(message) == {"role", "content"}, f"invalid message fields in {case['id']}")
        require(message["role"] in {"user", "assistant"} and isinstance(message["content"], str), f"invalid message in {case['id']}")
    expected_fields = {"answer_contains", "answer_excludes", "citations", "numeric_checks", "tool_calls", "clarification_required", "should_refuse"}
    require(set(case["expected"]) == expected_fields, f"invalid expected fields in {case['id']}")
    require(case["metadata"].get("difficulty") in {"easy", "medium", "hard"}, f"invalid difficulty in {case['id']}")
    require(isinstance(case["metadata"].get("tags"), list), f"invalid tags in {case['id']}")


def main() -> None:
    tables: dict[str, list[dict[str, str]]] = {}
    for filename, expected_header in EXPECTED_HEADERS.items():
        header, rows = read_csv(filename)
        require(header == expected_header, f"header mismatch: {filename}")
        require(rows, f"empty dataset: {filename}")
        tables[filename] = rows

    institutions = tables["institutions.csv"]
    products = tables["products.csv"]
    sales = tables["sales_weekly.csv"]
    feedback = tables["frontline_feedback.csv"]
    require(len(institutions) >= 12, "fewer than 12 institutions")
    require(len(institutions) == 16, "institution count must be 16")
    require(len(products) == 2, "product count must be 2")
    require(len(sales) == 256, "sales must contain 8 x 16 x 2 rows")
    require(len(feedback) == 48, "feedback count must be 48")

    org_map = {row["org_id"]: row for row in institutions}
    product_map = {row["product_id"]: row for row in products}
    require(len(org_map) == len(institutions), "duplicate org_id")
    require(len(product_map) == len(products), "duplicate product_id")
    require(set(row["region"] for row in institutions) == {"华东", "华北", "华南", "西部"}, "region set mismatch")
    require(Counter(row["region"] for row in institutions) == Counter({"华东": 4, "华北": 4, "华南": 4, "西部": 4}), "each region must have 4 institutions")

    week_starts = sorted({row["week_start"] for row in sales})
    require(len(week_starts) == 8, "sales must cover 8 unique weeks")
    require(week_starts[0] == "2026-07-06" and week_starts[-1] == "2026-08-24", "unexpected week range")
    for current, following in zip(week_starts, week_starts[1:]):
        require(date.fromisoformat(following) - date.fromisoformat(current) == timedelta(days=7), "weeks are not consecutive")
    unique_grain = {(row["week_start"], row["org_id"], row["product_id"]) for row in sales}
    require(len(unique_grain) == len(sales), "duplicate week-org-product row")

    for index, row in enumerate(sales, start=2):
        require(row["org_id"] in org_map, f"unknown org at sales row {index}")
        require(row["product_id"] in product_map, f"unknown product at sales row {index}")
        require(row["region"] == org_map[row["org_id"]]["region"], f"region mismatch at sales row {index}")
        require(int(row["list_price_cny"]) == int(product_map[row["product_id"]]["list_price_cny"]), f"list price mismatch at sales row {index}")
        discount = float(row["discount_rate"])
        expected_unit_price = int(round(int(row["list_price_cny"]) * (1 - discount) / 100.0) * 100)
        require(int(row["unit_price_cny"]) == expected_unit_price, f"unit price mismatch at sales row {index}")
        require(int(row["revenue_cny"]) == int(row["units"]) * int(row["unit_price_cny"]), f"revenue mismatch at sales row {index}")
        require(int(row["target_revenue_cny"]) > 0, f"nonpositive target at sales row {index}")
        funnel = [int(row[field]) for field in ["visits", "demos", "trials", "conversions"]]
        require(funnel == sorted(funnel, reverse=True), f"invalid funnel at sales row {index}")
        require(row["channel"] in {"直销", "合作伙伴"}, f"invalid channel at sales row {index}")

    feedback_ids = set()
    for index, row in enumerate(feedback, start=2):
        require(row["feedback_id"] not in feedback_ids, f"duplicate feedback_id at row {index}")
        feedback_ids.add(row["feedback_id"])
        require(row["org_id"] in org_map and row["product_id"] in product_map, f"feedback foreign key error at row {index}")
        require(row["region"] == org_map[row["org_id"]]["region"], f"feedback region mismatch at row {index}")
        require(row["sentiment"] in {"正向", "中性", "负向"}, f"invalid feedback sentiment at row {index}")
        require(row["urgency"] in {"低", "中", "高", "紧急"}, f"invalid feedback urgency at row {index}")

    knowledge_files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    require(4 <= len(knowledge_files) <= 7, "knowledge_base must contain 4-7 Markdown documents")
    for path in knowledge_files:
        text = path.read_text(encoding="utf-8")
        require(len(text) >= 300 and "#" in text, f"knowledge document is too short: {path.name}")

    json_cases = json.loads((BENCHMARK_DIR / "benchmark_120.json").read_text(encoding="utf-8"))
    jsonl_cases = [json.loads(line) for line in (BENCHMARK_DIR / "benchmark_120.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    require(isinstance(json_cases, list) and len(json_cases) == 120, "benchmark JSON must be an array of 120 cases")
    require(json_cases == jsonl_cases, "JSON and JSONL benchmark content differs")
    require([case["id"] for case in json_cases] == [f"BM{i:03d}" for i in range(1, 121)], "benchmark IDs are not contiguous")
    require(Counter(case["category"] for case in json_cases) == Counter(EXPECTED_CATEGORY_COUNTS), "benchmark category counts mismatch")
    for case in json_cases:
        validate_benchmark_schema(case)
    require(sum(1 for case in json_cases if case["category"] == "多轮" and len(case["input"]["messages"]) >= 3) == 10, "all multi-turn cases must have history")
    require(sum(1 for case in json_cases if case["category"] == "Tool参数" and case["expected"]["tool_calls"]) == 20, "tool cases must include expected calls")

    summary = {
        "status": "ok",
        "institutions": len(institutions),
        "products": len(products),
        "weeks": len(week_starts),
        "sales_rows": len(sales),
        "feedback_rows": len(feedback),
        "knowledge_documents": len(knowledge_files),
        "benchmark_cases": len(json_cases),
        "benchmark_categories": dict(Counter(case["category"] for case in json_cases)),
        "total_revenue_cny": sum(int(row["revenue_cny"]) for row in sales),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
