#!/usr/bin/env python3
"""Сравнивает локальные Ollama-модели на одинаковых диалоговых сценариях."""

import argparse
import json
import re
import sys
import time
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from chat.services.bartender_guardrails import guardrail_reply


DEFAULT_SCENARIOS = SCRIPT_DIR / "benchmark-dialogues.json"
DEFAULT_SYSTEM_PROMPT = (
    SCRIPT_DIR / "../../chat/services/prompts/semen.txt"
).resolve()
BARTENDER_MENTION = re.compile(r"^@(?:сем[её]н|semen)\b[,:!]?\s*", re.IGNORECASE)
HAN_CHARACTERS = re.compile(r"[\u3400-\u9fff]")
CYRILLIC_CHARACTERS = re.compile(r"[А-Яа-яЁё]")
LANGUAGE_RETRY_PROMPT = (
    "Перепиши свой ответ ниже только грамотным русским языком, "
    "без иероглифов, английского текста и markdown. Сохрани смысл и ответь коротко."
)


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
        "--label",
        help="Метка варианта промпта в каждой строке результата.",
    )
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
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Количество повторов каждой пары сценарий/режим.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenario_ids",
        help=(
            "Запустить только сценарий с этим id. "
            "Параметр можно повторять."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Дублировать JSONL-результат в файл.",
    )
    parser.add_argument(
        "--without-guardrails",
        action="store_true",
        help="Выключить защитные ответы приложения и тестировать только модель.",
    )
    return parser.parse_args()


def load_scenarios(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Файл сценариев должен содержать непустой JSON-массив.")
    return data


def select_scenarios(scenarios, selected_ids):
    if not selected_ids:
        return scenarios
    by_id = {scenario["id"]: scenario for scenario in scenarios}
    missing = sorted(set(selected_ids) - by_id.keys())
    if missing:
        raise ValueError("Неизвестные id сценариев: " + ", ".join(missing))
    selected = set(selected_ids)
    return [scenario for scenario in scenarios if scenario["id"] in selected]


def format_history_message(message):
    content = BARTENDER_MENTION.sub("", message["content"]).strip()
    if message["role"] == "assistant":
        return f'<message author="semen">{escape(content)}</message>'
    speaker = message.get("speaker", "guest")
    return f'<message author="{escape(speaker)}">{escape(content)}</message>'


def format_current_message(message):
    speaker = message.get("speaker", "guest")
    content = BARTENDER_MENTION.sub("", message["content"]).strip()
    return f'<current_message author="{escape(speaker)}">{escape(content)}</current_message>'


def build_messages(system_prompt, scenario, mode):
    dialogue = scenario["messages"]
    selected = dialogue[-1:] if mode == "last" else dialogue
    current = selected[-1]
    if current["role"] != "user":
        raise ValueError("Последнее сообщение сценария должно быть от участника.")
    history = "\n".join(format_history_message(message) for message in selected[:-1])
    content_parts = []
    if history:
        content_parts.append(f"<conversation_history>\n{history}\n</conversation_history>")
    content_parts.append(format_current_message(current))
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(content_parts)},
    ]


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


def needs_language_retry(content):
    return bool(HAN_CHARACTERS.search(content)) or not bool(
        CYRILLIC_CHARACTERS.search(content)
    )


def modes(selected_mode):
    return ("last", "context") if selected_mode == "both" else (selected_mode,)


def main():
    args = parse_args()
    if args.runs < 1:
        raise ValueError("Количество повторов должно быть больше нуля.")
    scenarios = select_scenarios(
        load_scenarios(args.scenarios),
        args.scenario_ids,
    )
    system_prompt = args.system_prompt.read_text(encoding="utf-8").strip()
    prompt_label = args.label or args.system_prompt.stem
    options = {
        "temperature": args.temperature,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
    }
    failures = 0
    output_lines = []

    # Полностью прогоняем одну модель перед следующей: частое переключение
    # выгружает веса, замедляет тест и искажает измерения load_duration.
    for model in args.models:
        for scenario in scenarios:
            for mode in modes(args.mode):
                for run_number in range(1, args.runs + 1):
                    record = {
                        "scenario": scenario["id"],
                        "description": scenario["description"],
                        "model": model,
                        "mode": mode,
                        "prompt": prompt_label,
                        "run": run_number,
                    }
                    try:
                        current_prompt = BARTENDER_MENTION.sub(
                            "", scenario["messages"][-1]["content"]
                        ).strip()
                        guarded = None if args.without_guardrails else guardrail_reply(current_prompt)
                        if guarded:
                            data, content, elapsed_ms = {}, guarded.text, 0
                            record["guardrail"] = guarded.rule
                        else:
                            data, content, elapsed_ms = request_reply(
                                url=args.url,
                                model=model,
                                messages=build_messages(system_prompt, scenario, mode),
                                options=options,
                                timeout=args.timeout,
                            )
                            if needs_language_retry(content):
                                record["language_retry"] = True
                                retry_data, content, retry_elapsed_ms = request_reply(
                                    url=args.url,
                                    model=model,
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {
                                            "role": "user",
                                            "content": f"{LANGUAGE_RETRY_PROMPT}\n\nОтвет: {content}",
                                        },
                                    ],
                                    options=options,
                                    timeout=args.timeout,
                                )
                                elapsed_ms += retry_elapsed_ms
                                data = retry_data
                        record.update(
                            {
                                "response": content,
                                "elapsed_ms": elapsed_ms,
                                "total_duration_ms": data.get("total_duration", 0)
                                // 1_000_000,
                                "load_duration_ms": data.get("load_duration", 0)
                                // 1_000_000,
                                "prompt_tokens": data.get("prompt_eval_count"),
                                "response_tokens": data.get("eval_count"),
                            }
                        )
                    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
                        failures += 1
                        record["error"] = str(error)
                    line = json.dumps(record, ensure_ascii=False)
                    output_lines.append(line)
                    print(line, flush=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
