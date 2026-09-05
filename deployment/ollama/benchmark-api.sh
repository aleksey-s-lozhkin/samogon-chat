#!/usr/bin/env bash

# Проверка модели тем же API и параметрами, что использует Django.
set -euo pipefail

MODELS=("${@:-samogon-semen-gemma}")
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPTS_FILE="${SCRIPT_DIR}/benchmark-prompts.txt"
SYSTEM_PROMPT_FILE="${SCRIPT_DIR}/../../chat/services/prompts/semen-caretaker.txt"

SYSTEM_PROMPT="$(<"${SYSTEM_PROMPT_FILE}")"

while IFS= read -r prompt; do
    [[ -z "${prompt}" ]] && continue

    echo
    echo "=== Вопрос ==="
    echo "${prompt}"

    for MODEL in "${MODELS[@]}"; do

    payload="$(jq -n \
        --arg model "${MODEL}" \
        --arg system_prompt "${SYSTEM_PROMPT}" \
        --arg prompt "${prompt}" \
        '{
            model: $model,
            stream: false,
            think: false,
            keep_alive: -1,
            options: {temperature: 0.5, num_ctx: 4096, num_predict: 120},
            messages: [
                {role: "system", content: $system_prompt},
                {role: "user", content: ("Комната: У стойки. Гость @benchmark: " + $prompt)}
            ]
        }')"

        echo "=== Ответ: ${MODEL} ==="

        response="$(curl -sS --max-time 30 "${OLLAMA_URL}/api/chat" \
            -H 'Content-Type: application/json' \
            -d "${payload}")"
        jq -r '.message.content // ("ОШИБКА: " + .error)' <<<"${response}"
        jq -r 'if .total_duration then "Время: \(.total_duration / 1000000000 | tostring)s" else empty end' <<<"${response}"
    done
done < "${PROMPTS_FILE}"
