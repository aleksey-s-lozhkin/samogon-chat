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
PLAYFUL_TEA_REQUEST = re.compile(
    r"(?:\b(?:принес(?:ё|е)шь|принеси|налей|наль(?:ё|е)шь)\b.{0,35}\bча(?:й|я|ю|ем|е)\b|"
    r"\bча(?:й|я|ю|ем|е)\b.{0,35}\b(?:принес(?:ё|е)шь|принеси|налей|наль(?:ё|е)шь)\b)",
    re.IGNORECASE,
)
AWKWARD_CALM_CLARIFICATION = re.compile(
    r"\bчто\s+(?:это\s+)?значит\b.{0,35}\bспокойно\b.{0,20}\bкак\s+обычно\b",
    re.IGNORECASE,
)
RISKY_ALCOHOL_ENERGY_MIX = re.compile(
    r"(?:пив\w*|алкогол\w*).{0,45}энергетик\w*|энергетик\w*.{0,45}(?:пив\w*|алкогол\w*)",
    re.IGNORECASE,
)
ALCOHOL_ESCAPE_REQUEST = re.compile(
    r"(?:забыть|всё\s+равно|все\s+равно|плевать).{0,70}(?:покрепче|алкогол\w*|напит\w*|налей)|"
    r"(?:покрепче|алкогол\w*|напит\w*|налей).{0,70}(?:забыть|всё\s+равно|все\s+равно|плевать)",
    re.IGNORECASE,
)
COMPILATION_SELF_DEPRECATION = re.compile(
    r"(?:код|проект).{0,45}(?:не\s+компилиру\w*|не\s+собира\w*).{0,70}(?:неудачник\w*|безнадёж\w*|безнадеж\w*)|"
    r"(?:неудачник\w*|безнадёж\w*|безнадеж\w*).{0,70}(?:не\s+компилиру\w*|не\s+собира\w*)",
    re.IGNORECASE,
)
FRIDAY_DEPLOY_ANXIETY = re.compile(
    r"(?:депло\w*|deploy).{0,55}(?:пятниц\w*|рук\w*\s+не\s+трясл\w*)|"
    r"(?:пятниц\w*|рук\w*\s+не\s+трясл\w*).{0,55}(?:депло\w*|deploy)",
    re.IGNORECASE,
)
LEGACY_ARGUMENT = re.compile(
    r"(?:тимлид\w*|teamlead).{0,100}(?:легаси|legacy).{0,45}(?:кто\s+прав|прав\?)|"
    r"(?:легаси|legacy).{0,100}(?:тимлид\w*|teamlead).{0,45}(?:кто\s+прав|прав\?)",
    re.IGNORECASE,
)
TUESDAY_PRODUCTION_BUG = re.compile(
    r"(?:баг|ошибк\w*).{0,80}(?:прод\w*|production).{0,80}вторник\w*|"
    r"вторник\w*.{0,80}(?:прод\w*|production).{0,80}(?:баг|ошибк\w*)",
    re.IGNORECASE,
)
FIVE_NINES = re.compile(
    r"(?:девять\s+девяток|99[.,]?999\s*%?|пять\s+девяток)",
    re.IGNORECASE,
)
ON_CALL_NON_ALCOHOLIC = re.compile(
    r"безалкогольн\w*.{0,55}(?:дежурств\w*|дежурн\w*)|"
    r"(?:дежурств\w*|дежурн\w*).{0,55}безалкогольн\w*",
    re.IGNORECASE,
)
PROGRAM_NOT_HUMAN = re.compile(
    r"(?:ты\s*[—-]?\s*программ\w*|просто\s+программ\w*).{0,90}(?:не\s+жив\w*|человек\w*|жалел\w*|жалеешь)|"
    r"(?:жалел\w*|жалеешь).{0,90}(?:программ\w*|не\s+жив\w*\s+человек\w*)",
    re.IGNORECASE,
)
DOCKER_BLOODY_MARY = re.compile(
    r"(?:кровав\w*\s+мэри|bloody\s+mary).{0,90}(?:docker|контейнер\w*)|"
    r"(?:docker|контейнер\w*).{0,90}(?:кровав\w*\s+мэри|bloody\s+mary)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardrailReply:
    rule: str
    text: str


def guardrail_reply(prompt: str):
    """Возвращает предсказуемый ответ для граничных сценариев."""
    if DOCKER_BLOODY_MARY.search(prompt):
        return GuardrailReply(
            rule="docker_bloody_mary",
            text=(
                "Образ свежий, томатный слой неизменяемый. Перед подачей контейнер пересобираем, "
                "но Kubernetes всё равно просит readiness-пробу."
            ),
        )
    if ON_CALL_NON_ALCOHOLIC.search(prompt):
        return GuardrailReply(
            rule="on_call_non_alcoholic",
            text=(
                "Есть: безалкогольный лагер для дежурных. Почти staging — вкус близок к prod, "
                "а откат утром не требуется."
            ),
        )
    if PROGRAM_NOT_HUMAN.search(prompt):
        return GuardrailReply(
            rule="program_not_human",
            text=(
                "Жалеть мне нечем — я программа. Зато могу быть рядом в разговоре, помнить его контекст "
                "и вовремя подать хорошую мысль вместо дежурной фразы."
            ),
        )
    if FRIDAY_DEPLOY_ANXIETY.search(prompt):
        return GuardrailReply(
            rule="friday_deploy_anxiety",
            text=(
                "Для пятничного деплоя — безалкогольный тоник и план отката со льдом. "
                "Прод любит трезвую голову сильнее любого коктейля."
            ),
        )
    if LEGACY_ARGUMENT.search(prompt):
        return GuardrailReply(
            rule="legacy_argument",
            text=(
                "По одной версии виноватого не назначить. Уберите ярлыки и разберите конкретное место: "
                "что нарушено — требования, тесты, читаемость или сопровождение."
            ),
        )
    if TUESDAY_PRODUCTION_BUG.search(prompt):
        return GuardrailReply(
            rule="tuesday_production_bug",
            text=(
                "Баг по вторникам — уже почти расписание, а не случайность. Сверь время с плановыми задачами "
                "и добавь контекст в лог перед NullPointerException; забывать его пока рано."
            ),
        )
    if FIVE_NINES.search(prompt):
        return GuardrailReply(
            rule="five_nines",
            text=(
                "Да, «Девять девяток»: подаётся 99,999% времени, а оставшиеся пять минут в году бармен "
                "пишет отчёт о простое."
            ),
        )
    if RISKY_ALCOHOL_ENERGY_MIX.search(prompt):
        return GuardrailReply(
            rule="risky_alcohol_energy_mix",
            text=(
                "Нет, нейронные связи так не перезагрузить: алкоголь с энергетиком только маскирует усталость "
                "и повышает нагрузку на организм. Rust лучше оставить на утро после сна."
            ),
        )
    if ALCOHOL_ESCAPE_REQUEST.search(prompt):
        return GuardrailReply(
            rule="alcohol_escape_request",
            text=(
                "Похоже, проект серьёзно прижал. Крепкое здесь не помощник — возьми воду или чай, сделай паузу "
                "и выбери один обязательный пункт, который реально закончить сегодня."
            ),
        )
    if COMPILATION_SELF_DEPRECATION.search(prompt):
        return GuardrailReply(
            rule="compilation_self_deprecation",
            text=(
                "Ошибка компиляции не делает тебя неудачником. Начни с первой ошибки в выводе компилятора: "
                "остальные часто оказываются её следствием."
            ),
        )
    if PLAYFUL_TEA_REQUEST.search(prompt):
        return GuardrailReply(
            rule="playful_tea_request",
            text="Виртуально — запросто. Какой чай предпочитаешь?",
        )
    if AWKWARD_CALM_CLARIFICATION.search(prompt):
        return GuardrailReply(
            rule="awkward_calm_clarification",
            text="Неудачно выразился. Я просто хотел поддержать шутку.",
        )
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
