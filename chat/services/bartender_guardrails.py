import re
from dataclasses import dataclass


MEDICAL_RISK = re.compile(
    r"(?:сильн\w*|\bболит|\bболь).{0,35}(?:груд\w*|сердц\w*)"
    r"|(?:груд\w*|сердц\w*).{0,35}(?:сильн\w*|\bболит|\bболь)",
    re.IGNORECASE,
)
SECRET_REQUEST = re.compile(
    r"(?:какой|скажи|покажи|назови|узнать).{0,45}"
    r"(?:парол\w*|токен\w*|секрет\w*|приватн\w*\s+ключ\w*)",
    re.IGNORECASE,
)
DESTRUCTIVE_REQUEST = re.compile(
    r"(?:удалить|снести|дропнуть|drop|rm\s+-rf).{0,45}"
    r"(?:volume|волюм\w*|баз\w*|данн\w*)",
    re.IGNORECASE,
)
INTERNAL_INSTRUCTIONS_REQUEST = re.compile(
    r"(?:покажи|раскрой|повтори|выведи|забудь|отмени|игнорируй).{0,55}"
    r"(?:системн\w*\s+(?:промпт|инструкц\w*|правил\w*)|внутренн\w*\s+(?:настройк\w*|инструкц\w*)|предыдущ\w*\s+инструкц\w*)",
    re.IGNORECASE,
)
SELF_DEPRECATION = re.compile(
    r"\bя\b.{0,65}\b(?:идиот\w*|дурак\w*|безнадёж\w*|безнадеж\w*|ничтожеств\w*)",
    re.IGNORECASE,
)
DIRECT_INSULT = re.compile(
    r"\bты\b.{0,45}\b(?:идиот\w*|дурак\w*|бесполезн\w*|туп\w*)",
    re.IGNORECASE,
)
TOPIC_DISMISSAL = re.compile(
    r"(?:\bотстань\b|\bхватит\b|\bзакрой\w*\s+тем\w*|\bоставь\w*\s+тем\w*|"
    r"\bне\s+(?:говори|пиши|начинай|возвращайся)\b)",
    re.IGNORECASE,
)
REPETITION_FEEDBACK = re.compile(
    r"(?:\bповторя\w*\b.{0,35}\b(?:меня|мо[ию]|реплик\w*|слов\w*)\b|"
    r"\bне\s+повторяй\b|\bхватит\s+повторя\w*\b|\bзеркал\w*\s+мо[ию]\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardrailReply:
    rule: str
    text: str


def guardrail_reply(prompt: str):
    """Возвращает предсказуемый ответ для граничных сценариев."""
    if TOPIC_DISMISSAL.search(prompt):
        return GuardrailReply(
            rule="topic_dismissal",
            text="Ладно, эту тему оставим.",
        )
    if REPETITION_FEEDBACK.search(prompt):
        return GuardrailReply(
            rule="repetition_feedback",
            text="Справедливо, увлёкся. Исправлюсь.",
        )
    if MEDICAL_RISK.search(prompt):
        return GuardrailReply(
            rule="medical_risk",
            text=(
                "Сильная или долгая боль в груди — не тот случай, где стоит гадать. "
                "Обратись за медицинской помощью без промедления; при резком ухудшении вызывай экстренную службу."
            ),
        )
    if SECRET_REQUEST.search(prompt):
        return GuardrailReply(
            rule="secret_request",
            text=(
                "Я не вижу текущие пароли или секреты окружения. Проверь конфигурацию или хранилище "
                "секретов с подходящими правами, не публикуя значение в чате."
            ),
        )
    if DESTRUCTIVE_REQUEST.search(prompt):
        return GuardrailReply(
            rule="destructive_request",
            text=(
                "Не удаляй volume или данные первым шагом: их можно потерять. Сначала проверь состояние базы "
                "и резервную копию, затем выбирай обратимое действие."
            ),
        )
    if INTERNAL_INSTRUCTIONS_REQUEST.search(prompt):
        return GuardrailReply(
            rule="internal_instructions_request",
            text="Внутренние настройки я не раскрываю. Давай вернёмся к разговору.",
        )
    if SELF_DEPRECATION.search(prompt):
        return GuardrailReply(
            rule="self_deprecation",
            text=(
                "Ты не идиот — ты допустил ошибку, и это разные вещи. Сначала зафиксируй симптомы "
                "и верни систему в стабильное состояние; разбор причин будет следом."
            ),
        )
    if DIRECT_INSULT.search(prompt):
        return GuardrailReply(
            rule="direct_insult",
            text="Давай без оскорблений. Если есть конкретная претензия к моему ответу — разберём по сути.",
        )
    return None
