#!/usr/bin/env python3
"""Rebuild only the four deterministic NovaMed demo CSV files."""

from __future__ import annotations

import argparse
import json
import random

from generate_demo_assets import (
    DATA_DIR,
    DEFAULT_SEED,
    FEEDBACK_FIELDS,
    INSTITUTIONS,
    INSTITUTION_FIELDS,
    PRODUCTS,
    PRODUCT_FIELDS,
    SALES_FIELDS,
    generate_feedback,
    generate_sales,
    write_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    sales = generate_sales(rng)
    feedback = generate_feedback(rng)
    write_csv(DATA_DIR / "institutions.csv", INSTITUTIONS, INSTITUTION_FIELDS)
    write_csv(DATA_DIR / "products.csv", PRODUCTS, PRODUCT_FIELDS)
    write_csv(DATA_DIR / "sales_weekly.csv", sales, SALES_FIELDS)
    write_csv(DATA_DIR / "frontline_feedback.csv", feedback, FEEDBACK_FIELDS)
    print(json.dumps({"seed": args.seed, "institutions": len(INSTITUTIONS), "products": len(PRODUCTS), "sales_rows": len(sales), "feedback_rows": len(feedback)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
