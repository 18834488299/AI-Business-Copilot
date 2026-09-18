"""CSV loading, joins, canonical fields, and deterministic filtering.

The demo ships with NovaMed-specific files, but the loader also recognizes a
small set of common Chinese/English column aliases so the agent remains useful
when the sample data is replaced.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import Source


ALIASES: Dict[str, Tuple[str, ...]] = {
    "date": (
        "week_start",
        "date",
        "sale_date",
        "order_date",
        "日期",
        "周开始",
        "week",
    ),
    "date_end": ("week_end", "end_date", "周结束"),
    "region": ("region", "area", "territory", "区域", "大区"),
    "province": ("province", "省份", "省"),
    "city": ("city", "城市", "市"),
    "institution": (
        "org_name",
        "institution",
        "institution_name",
        "hospital",
        "customer",
        "机构",
        "机构名称",
        "医院",
    ),
    "institution_id": ("org_id", "institution_id", "customer_id", "机构id"),
    "institution_type": ("institution_type", "org_type", "机构类型"),
    "tier": ("tier", "level", "grade", "机构分级", "等级"),
    "target_segment": ("target_segment", "segment", "客户分层", "目标分层"),
    "product": ("product_name", "product", "sku_name", "产品", "产品名称"),
    "product_id": ("product_id", "sku", "sku_id", "产品id"),
    "category": ("category", "product_category", "品类", "产品类别"),
    "amount": (
        "revenue_cny",
        "sales_amount",
        "revenue",
        "sales",
        "amount",
        "销售额",
        "销售金额",
    ),
    "target": (
        "target_revenue_cny",
        "target_amount",
        "sales_target",
        "target",
        "销售目标",
        "目标销售额",
    ),
    "units": ("units", "quantity", "qty", "volume", "销量", "数量"),
    "visits": ("visits", "拜访量", "拜访次数"),
    "demos": ("demos", "演示量", "演示次数"),
    "trials": ("trials", "试用量", "试用次数"),
    "conversions": ("conversions", "转化量", "成交数"),
    "returns": ("returns", "退货量", "退货数"),
    "discount_rate": ("discount_rate", "discount", "折扣率"),
    "list_price": ("list_price_cny", "list_price", "目录价"),
    "unit_price": ("unit_price_cny", "unit_price", "成交单价"),
    "channel": ("channel", "渠道"),
}


def _column_key(value: str) -> str:
    return re.sub(r"[\s\-_/（）()]+", "", str(value)).casefold()


def _find_column(fieldnames: Iterable[str], aliases: Sequence[str]) -> Optional[str]:
    lookup = {_column_key(name): name for name in fieldnames}
    for alias in aliases:
        if _column_key(alias) in lookup:
            return lookup[_column_key(alias)]
    return None


def parse_number(value: Any) -> float:
    """Parse currency/percentage-like CSV values without locale dependencies."""

    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    text = str(value).strip()
    if not text or text.casefold() in {"na", "n/a", "null", "none", "-"}:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier, text = 100_000_000.0, text[:-1]
    elif text.endswith("万"):
        multiplier, text = 10_000.0, text[:-1]
    percent = text.endswith("%")
    text = re.sub(r"[¥￥$,，%\s()]", "", text)
    try:
        result = float(text) * multiplier
        if percent:
            result /= 100.0
        return -result if negative else result
    except ValueError:
        return 0.0


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m", "%Y/%m", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    iso_week = re.fullmatch(r"(\d{4})[-年]?W?(\d{1,2})周?", text, re.IGNORECASE)
    if iso_week:
        try:
            return date.fromisocalendar(int(iso_week.group(1)), int(iso_week.group(2)), 1)
        except ValueError:
            return None
    return None


@dataclass
class CSVTable:
    path: Path
    rows: List[Dict[str, str]]
    fieldnames: Tuple[str, ...]

    @property
    def name(self) -> str:
        return self.path.name


class DataCatalog:
    """Load all CSVs and expose a joined, canonical sales fact table."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.tables: Dict[str, CSVTable] = {}
        self.sales: List[Dict[str, Any]] = []
        self.sales_file = ""
        self.refresh()

    def refresh(self) -> None:
        self.tables = {}
        if self.data_dir.exists():
            for path in sorted(self.data_dir.glob("*.csv")):
                table = self._load_csv(path)
                self.tables[path.name] = table
        self.sales_file, raw_sales = self._choose_sales_table()
        self.sales = self._canonicalize_sales(raw_sales)

    @staticmethod
    def _load_csv(path: Path) -> CSVTable:
        rows: List[Dict[str, str]] = []
        fieldnames: Tuple[str, ...] = ()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = tuple(reader.fieldnames or ())
                for row in reader:
                    if any(str(value or "").strip() for value in row.values()):
                        rows.append({str(key): str(value or "").strip() for key, value in row.items()})
        except (OSError, UnicodeError, csv.Error):
            # An unreadable optional file must not take down the whole assistant.
            pass
        return CSVTable(path=path, rows=rows, fieldnames=fieldnames)

    def _choose_sales_table(self) -> Tuple[str, List[Dict[str, str]]]:
        best: Tuple[int, str, List[Dict[str, str]]] = (-1, "", [])
        for name, table in self.tables.items():
            fields = table.fieldnames
            score = 0
            if _find_column(fields, ALIASES["amount"]):
                score += 10
            if _find_column(fields, ALIASES["date"]):
                score += 3
            for dimension in ("region", "institution", "institution_id", "product", "product_id"):
                if _find_column(fields, ALIASES[dimension]):
                    score += 2
            if "sales" in name.casefold() or "销售" in name:
                score += 4
            if score > best[0]:
                best = (score, name, table.rows)
        return best[1], best[2]

    def _dimension_index(self, kind: str) -> Tuple[Dict[str, Dict[str, str]], Optional[str]]:
        id_aliases = ALIASES[f"{kind}_id"]
        name_aliases = ALIASES[kind]
        best: Tuple[int, Dict[str, Dict[str, str]], Optional[str]] = (-1, {}, None)
        for filename, table in self.tables.items():
            if filename == self.sales_file:
                continue
            id_col = _find_column(table.fieldnames, id_aliases)
            name_col = _find_column(table.fieldnames, name_aliases)
            if not id_col or not name_col:
                continue
            score = len(table.rows)
            index = {row.get(id_col, ""): row for row in table.rows if row.get(id_col, "")}
            if score > best[0]:
                best = (score, index, filename)
        return best[1], best[2]

    def _canonicalize_sales(self, raw_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        if not raw_rows:
            return []
        institution_index, institution_file = self._dimension_index("institution")
        product_index, product_file = self._dimension_index("product")
        fields = tuple(raw_rows[0].keys())
        column_map = {name: _find_column(fields, aliases) for name, aliases in ALIASES.items()}
        canonical: List[Dict[str, Any]] = []
        for raw in raw_rows:
            joined: Dict[str, Any] = dict(raw)
            org_id_col = column_map.get("institution_id")
            product_id_col = column_map.get("product_id")
            if org_id_col and raw.get(org_id_col, "") in institution_index:
                for key, value in institution_index[raw[org_id_col]].items():
                    joined.setdefault(key, value)
            if product_id_col and raw.get(product_id_col, "") in product_index:
                for key, value in product_index[raw[product_id_col]].items():
                    joined.setdefault(key, value)
            joined_fields = tuple(joined.keys())

            def pick(field: str) -> str:
                column = _find_column(joined_fields, ALIASES[field])
                return str(joined.get(column, "")).strip() if column else ""

            parsed_date = parse_date(pick("date"))
            parsed_date_end = parse_date(pick("date_end"))
            row: Dict[str, Any] = {
                "date": parsed_date.isoformat() if parsed_date else pick("date"),
                "date_end": parsed_date_end.isoformat() if parsed_date_end else pick("date_end"),
                "_date": parsed_date,
                "region": pick("region"),
                "province": pick("province"),
                "city": pick("city"),
                "institution": pick("institution") or pick("institution_id"),
                "institution_id": pick("institution_id"),
                "institution_type": pick("institution_type"),
                "tier": pick("tier"),
                "target_segment": pick("target_segment"),
                "product": pick("product") or pick("product_id"),
                "product_id": pick("product_id"),
                "category": pick("category"),
                "amount": parse_number(pick("amount")),
                "target": parse_number(pick("target")),
                "units": parse_number(pick("units")),
                "visits": parse_number(pick("visits")),
                "demos": parse_number(pick("demos")),
                "trials": parse_number(pick("trials")),
                "conversions": parse_number(pick("conversions")),
                "returns": parse_number(pick("returns")),
                "discount_rate": parse_number(pick("discount_rate")),
                "list_price": parse_number(pick("list_price")),
                "unit_price": parse_number(pick("unit_price")),
                "channel": pick("channel"),
                "_source_file": self.sales_file,
                "_dimension_files": [name for name in (institution_file, product_file) if name],
            }
            canonical.append(row)
        return canonical

    def entities(self) -> Dict[str, List[str]]:
        values: Dict[str, List[str]] = {}
        for field in ("region", "province", "city", "institution", "product", "category", "tier", "target_segment"):
            values[field] = sorted(
                {str(row.get(field, "")) for row in self.sales if str(row.get(field, "")).strip()},
                key=lambda item: (-len(item), item),
            )
        return values

    def date_range(self) -> Tuple[Optional[date], Optional[date]]:
        dates = [row["_date"] for row in self.sales if row.get("_date")]
        return (min(dates), max(dates)) if dates else (None, None)

    def filter_sales(self, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        filters = dict(filters or {})
        rows = list(self.sales)
        plural_fields = {
            "regions": "region",
            "products": "product",
            "institutions": "institution",
            "provinces": "province",
            "cities": "city",
            "tiers": "tier",
        }
        for key, field in plural_fields.items():
            values = filters.get(key) or []
            if isinstance(values, str):
                values = [values]
            wanted = {str(item).strip().casefold() for item in values if str(item).strip()}
            if wanted:
                rows = [row for row in rows if str(row.get(field, "")).casefold() in wanted]

        start = parse_date(filters.get("start_date"))
        end = parse_date(filters.get("end_date"))
        if start:
            rows = [row for row in rows if row.get("_date") and row["_date"] >= start]
        if end:
            rows = [row for row in rows if row.get("_date") and row["_date"] <= end]

        relative_weeks = int(filters.get("relative_weeks") or 0)
        if relative_weeks > 0:
            dated = [row["_date"] for row in rows if row.get("_date")]
            if dated:
                threshold = max(dated) - timedelta(days=7 * (relative_weeks - 1))
                rows = [row for row in rows if row.get("_date") and row["_date"] >= threshold]
        return rows

    def data_source(
        self,
        rows_used: int,
        *,
        include_dimensions: bool = False,
        description: str = "经筛选并聚合的销售事实数据",
    ) -> List[Source]:
        if not self.sales_file:
            return []
        names = [self.sales_file]
        if include_dimensions and self.sales:
            names.extend(self.sales[0].get("_dimension_files", []))
        sources: List[Source] = []
        for index, name in enumerate(dict.fromkeys(names)):
            sources.append(
                Source(
                    id=f"DATA:{name}",
                    type="structured_data",
                    file=f"data/{name}",
                    citation=f"[DATA:{name}]",
                    description=description if index == 0 else "维表关联后的名称与属性",
                    rows_used=rows_used if index == 0 else 0,
                )
            )
        return sources

    def status(self) -> Dict[str, Any]:
        start, end = self.date_range()
        return {
            "data_dir": str(self.data_dir),
            "files": sorted(self.tables),
            "sales_file": self.sales_file,
            "sales_rows": len(self.sales),
            "date_range": [start.isoformat() if start else None, end.isoformat() if end else None],
            "entities": {key: len(value) for key, value in self.entities().items()},
        }
