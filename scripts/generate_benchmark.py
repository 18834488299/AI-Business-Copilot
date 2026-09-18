#!/usr/bin/env python3
"""Rebuild only the deterministic 120-case NovaMed benchmark."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from generate_demo_assets import (
    BENCHMARK_DIR,
    DATE_FROM,
    DATE_TO,
    DEFAULT_SEED,
    WEEKS,
    build_benchmark,
    generate_feedback,
    generate_sales,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    sales = generate_sales(rng)
    feedback = generate_feedback(rng)
    benchmark = build_benchmark(sales, feedback)
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIR / "benchmark_120.json").write_text(json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BENCHMARK_DIR / "benchmark_120.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in benchmark), encoding="utf-8")
    manifest = {
        "name": "NovaMed AI Business Copilot Benchmark",
        "fictional": True,
        "seed": args.seed,
        "total": len(benchmark),
        "category_counts": dict(Counter(case["category"] for case in benchmark)),
        "data_period": {"from": DATE_FROM, "to": DATE_TO, "weeks": len(WEEKS)},
        "generated_files": ["benchmark/benchmark_120.json", "benchmark/benchmark_120.jsonl"],
    }
    (BENCHMARK_DIR / "benchmark_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
