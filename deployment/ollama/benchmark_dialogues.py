#!/usr/bin/env python3
"""Сравнивает локальные Ollama-модели на одинаковых диалоговых сценариях."""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = SCRIPT_DIR / "benchmark-dialogues.json"
DEFAULT_SYSTEM_PROMPT = (
    SCRIPT_DIR / "../../chat/services/prompts/semen-caretaker.txt"
).resolve()
BARTENDER_MENTION = re.compile(r"^@(?:сем[её]н|semen)\b[,:!]?\s*", re.IGNORECASE)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Сравнение моделей Семёна в режимах без истории и с контекстом."
    )
    parser.add_argument("models", nargs="+", help="Имена моделей из Ollama /api/tags.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:11434",
        help="Базовый URL Ollama API.",
    )
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument(
        "--mode",
        choices=("last", "context", "both"),
        default="both",
        help="last повторяет текущее поведение; context передаёт историю.",
    )
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=120)
    parser.add_argument("--timeout", type=float, default=60)
    return parser.parse_args()


def load_scenarios(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Файл сценариев должен содержать непустой JSON-массив.")
    return data


def format_user_message(message):
    speaker = message.get("speaker", "guest")
    content = BARTENDER_MENTION.sub("", message["content"]).strip()
    return f"Комната: У стойки. Гость @{speaker}: {content}"


def build_messages(system_prompt, scenario, mode):
    dialogue = scenario["messages"]
    selected = dialogue[-1:] if mode == "last" else dialogue
    messages = [{"role": "system", "content": system_prompt}]
    for message in selected:
        content = message["content"]
        if message["role"] == "user":
            content = format_user_message(message)
        messages.append({"role": message["role"], "content": content})
    return messages


def request_reply(*, url, model, messages, options, timeout):
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": options,
        "messages": messages,
    }
    request = Request(
        f"{url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    content = data.get("message", {}).get("content", "").strip()
    if not content:
        raise ValueError(data.get("error", "Ollama вернула пустой ответ."))
    return data, content, elapsed_ms


def modes(selected_mode):
    return ("last", "context") if selected_mode == "both" else (selected_mode,)


def main():
    args = parse_args()
    scenarios = load_scenarios(args.scenarios)
    system_prompt = args.system_prompt.read_text(encoding="utf-8").strip()
    options = {
        "temperature": args.temperature,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
    }
    failures = 0

    for scenario in scenarios:
        for model in args.models:
            for mode in modes(args.mode):
                record = {
                    "scenario": scenario["id"],
                    "description": scenario["description"],
                    "model": model,
                    "mode": mode,
                }
                try:
                    data, content, elapsed_ms = request_reply(
                        url=args.url,
                        model=model,
                        messages=build_messages(system_prompt, scenario, mode),
                        options=options,
                        timeout=args.timeout,
                    )
                    record.update(
                        {
                            "response": content,
                            "elapsed_ms": elapsed_ms,
                            "total_duration_ms": data.get("total_duration", 0) // 1_000_000,
                            "load_duration_ms": data.get("load_duration", 0) // 1_000_000,
                            "prompt_tokens": data.get("prompt_eval_count"),
                            "response_tokens": data.get("eval_count"),
                        }
                    )
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                    failures += 1
                    record["error"] = str(error)
                print(json.dumps(record, ensure_ascii=False), flush=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
