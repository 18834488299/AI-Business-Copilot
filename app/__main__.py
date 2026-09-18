"""Minimal dependency-free CLI: ``python -m app '全国销售摘要'``."""

from __future__ import annotations

import argparse
import json

from .agent import BusinessCopilot


def main() -> None:
    parser = argparse.ArgumentParser(description="NovaMed AI Business Copilot")
    parser.add_argument("question", nargs="?", default="请生成最新一周经营周报")
    parser.add_argument("--mode", choices=("local", "auto", "openai"), default="local")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = BusinessCopilot(mode=args.mode).chat(args.question)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json else result["answer"])


if __name__ == "__main__":
    main()
