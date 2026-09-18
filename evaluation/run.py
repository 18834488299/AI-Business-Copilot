"""Run the 120-case offline benchmark.

Example::

    python -m evaluation.run --mode local
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from app.agent import BusinessCopilot
from evaluation.metrics import aggregate, score_case


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_benchmark(path: Path) -> List[Dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Benchmark must be a JSON array")
    return payload


def run_case(agent: BusinessCopilot, case: Mapping[str, Any]) -> Dict[str, Any]:
    session_id = f"benchmark-{case['id']}"
    result: Dict[str, Any] = {}
    start = time.perf_counter()
    for message in case["input"]["messages"]:
        # The fixture's assistant turns document expected history. The agent creates
        # its own preceding response from the user turns, avoiding answer leakage.
        if message["role"] == "user":
            result = agent.chat(message["content"], session_id=session_id)
    latency_ms = (time.perf_counter() - start) * 1_000
    return score_case(case, result, latency_ms)


def run_benchmark(
    benchmark: Iterable[Mapping[str, Any]],
    mode: str = "local",
    limit: int = 0,
) -> Dict[str, Any]:
    agent = BusinessCopilot(mode=mode)
    cases = list(benchmark)
    if limit:
        cases = cases[:limit]
    scores = [run_case(agent, case) for case in cases]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "summary": aggregate(scores),
        "cases": scores,
    }


def print_summary(report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    print(f"NovaMed benchmark · {report['mode']} mode · {summary['total_cases']} cases")
    print("-" * 68)
    for name, metric in summary["metrics"].items():
        print(f"{name:28} {metric['rate'] * 100:6.1f}%  ({metric['passed']}/{metric['total']})")
    latency = summary["latency_ms"]
    print(f"{'average latency':28} {latency['average']:6.1f} ms")
    print(f"{'p95 latency':28} {latency['p95']:6.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate NovaMed AI Business Copilot")
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=PROJECT_ROOT / "benchmark" / "benchmark_120.json",
    )
    parser.add_argument("--mode", choices=["local", "openai"], default="local")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "results_latest.json",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    report = run_benchmark(load_benchmark(args.benchmark), args.mode, args.limit)
    print_summary(report)
    if not args.no_write:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nDetailed report: {args.output}")


if __name__ == "__main__":
    main()
