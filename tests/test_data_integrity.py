import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_csv(name):
    with (ROOT / "data" / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_sales_fixture_shape_and_foreign_keys():
    institutions = read_csv("institutions.csv")
    products = read_csv("products.csv")
    sales = read_csv("sales_weekly.csv")
    assert len(institutions) == 16
    assert len(products) == 2
    assert len(sales) == 8 * 16 * 2

    institution_by_id = {item["org_id"]: item for item in institutions}
    product_ids = {item["product_id"] for item in products}
    for row in sales:
        assert row["org_id"] in institution_by_id
        assert row["product_id"] in product_ids
        assert row["region"] == institution_by_id[row["org_id"]]["region"]


def test_sales_money_is_internally_consistent():
    for row in read_csv("sales_weekly.csv"):
        units = int(row["units"])
        unit_price = float(row["unit_price_cny"])
        revenue = float(row["revenue_cny"])
        assert abs(units * unit_price - revenue) < 0.02


def test_feedback_references_known_entities():
    institution_ids = {item["org_id"] for item in read_csv("institutions.csv")}
    product_ids = {item["product_id"] for item in read_csv("products.csv")}
    feedback = read_csv("frontline_feedback.csv")
    assert feedback
    assert all(row["org_id"] in institution_ids for row in feedback)
    assert all(row["product_id"] in product_ids for row in feedback)
