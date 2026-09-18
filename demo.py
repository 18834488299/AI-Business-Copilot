"""Small terminal demo that works without an API key."""

import argparse
import json

from app.agent import BusinessCopilot


def main() -> None:
    parser = argparse.ArgumentParser(description="NovaMed AI Business Copilot")
    parser.add_argument("question", nargs="*", help="Question to ask the agent")
    parser.add_argument("--mode", choices=["local", "openai"], default="local")
    parser.add_argument("--json", action="store_true", help="Print the full structured result")
    args = parser.parse_args()

    question = " ".join(args.question).strip() or "华东为什么下降？"
    result = BusinessCopilot(mode=args.mode).chat(question)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result["answer"])
    if result.get("sources"):
        print("\nSources:")
        for source in result["sources"]:
            label = source.get("title") or source.get("source") or str(source)
            print(f"- {label}")


if __name__ == "__main__":
    main()
