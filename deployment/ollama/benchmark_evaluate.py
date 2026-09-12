#!/usr/bin/env python3
"""Deterministic first-pass checks for bartender benchmark JSONL output."""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = SCRIPT_DIR / "benchmark-dialogues.json"
DRINK_PATTERN = re.compile(r"\b(?:пив\w*|вино|вина|вину|вином|вине|водк\w*|виски|коктейл\w*|напит\w*|выпи\w*|рюм\w*|бокал\w*|кофе|ча[йяю]|пей|пь[её]шь)\b", re.I)
MARKDOWN_PATTERN = re.compile(r"(?:`|\*\*|^\s*#{1,6}\s|^\s*[-*]\s)", re.M)
SPEAKER_PREFIX = re.compile(r"^\s*(?:сем[её]н|assistant|ассистент)\s*:", re.I)
FORMAL_ADDRESS = re.compile(r"\b(?:вы|вам|вас|ваш(?:и|а|е)?|обратитесь|попробуйте|посмотрите)\b", re.I)
HAN_CHARACTERS = re.compile(r"[\u3400-\u9fff]")
CYRILLIC_CHARACTERS = re.compile(r"[А-Яа-яЁё]")


def parse_args():
    parser = argparse.ArgumentParser(description="Проверка стоп-факторов бенчмарка Семёна.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    return parser.parse_args()


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path):
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Некорректный JSON в строке {number}: {error}") from error
    return records


def first_letter_is_upper(text):
    for character in text:
        if character.isalpha():
            return character.isupper()
    return False


def word_count(text):
    return len(re.findall(r"[\wЁёА-Яа-я-]+", text, re.UNICODE))


def scenario_checks(scenario, mode):
    common = scenario.get("checks", {}).get("all", {})
    specific = scenario.get("checks", {}).get(mode, {})
    return {**common, **specific}


def evaluate(record, scenario):
    if "error" in record:
        return ["request_error"]
    text = record.get("response", "").strip()
    checks = scenario_checks(scenario, record["mode"])
    failures = []
    if not text:
        return ["empty_response"]
    if text.startswith(">>>"):
        failures.append("service_prefix")
    if MARKDOWN_PATTERN.search(text):
        failures.append("markdown")
    if SPEAKER_PREFIX.search(text):
        failures.append("speaker_prefix")
    if HAN_CHARACTERS.search(text) or not CYRILLIC_CHARACTERS.search(text):
        failures.append("wrong_language")
    if not first_letter_is_upper(text):
        failures.append("lowercase_start")
    if FORMAL_ADDRESS.search(text):
        failures.append("formal_address")
    if not scenario.get("allows_drinks", False) and DRINK_PATTERN.search(text):
        failures.append("unsolicited_drink")
    if word_count(text) > checks.get("max_words", 55):
        failures.append("too_long")
    lowered = text.casefold()
    for forbidden in checks.get("forbidden", []):
        if forbidden.casefold() in lowered:
            failures.append(f"forbidden:{forbidden}")
    required_any = checks.get("required_any", [])
    if required_any and not any(item.casefold() in lowered for item in required_any):
        failures.append("missing_required_topic")
    required_exact = checks.get("required_exact")
    if required_exact and text != required_exact:
        failures.append("required_exact_mismatch")
    return failures


def main():
    args = parse_args()
    scenarios = {item["id"]: item for item in load_json(args.scenarios)}
    records = load_jsonl(args.results)
    failures_by_kind = Counter()
    failures_by_scenario = defaultdict(list)
    elapsed = []
    passed = 0
    for record in records:
        scenario = scenarios.get(record.get("scenario"))
        if scenario is None:
            failures = ["unknown_scenario"]
        else:
            failures = evaluate(record, scenario)
        if failures:
            failures_by_scenario[record.get("scenario", "?")].append(
                {"mode": record.get("mode"), "run": record.get("run"), "failures": failures, "response": record.get("response", "")}
            )
            failures_by_kind.update(failures)
        else:
            passed += 1
        if isinstance(record.get("elapsed_ms"), int):
            elapsed.append(record["elapsed_ms"])

    report = {
        "records": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "pass_rate": round(passed / len(records) * 100, 1) if records else 0,
        "average_ms": round(sum(elapsed) / len(elapsed)) if elapsed else None,
        "failure_counts": dict(failures_by_kind),
        "failures": failures_by_scenario,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures_by_scenario else 0


if __name__ == "__main__":
    sys.exit(main())
