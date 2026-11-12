"""Настоящий случайный выбор вместо «самого вероятного».

Модель не подбрасывает кубик: на просьбу «назови случайное число» она выдаёт
самое вероятное. Проверено на нашем qwen3:14b, десять прогонов на диапазон:
1–10 — «семь» десять раз, 1–30 — «17» десять раз, 1–100 — «семьдесят три».
Это известное поведение всех языковых моделей, и оно выдаёт в персонаже
калькулятор, а не бармена.

Поэтому число выбирает сервер — через системный генератор, — а Семён его
подаёт. Так «случайное» снова случайное, а ответ остаётся в характере.
"""

from __future__ import annotations

import random
import re

#: Просьба именно о случайном числе.
RANDOM_NUMBER = re.compile(r"случайн\w*\s+числ|числ\w*\s+наугад", re.IGNORECASE)

#: Диапазон: «от 1 до 30», «1-30», «1 — 30», «1..30».
RANGE = re.compile(
    r"(?:от\s*)?(\d+)\s*(?:до|\.\.\.?|—|–|-)\s*(\d+)",
    re.IGNORECASE,
)

#: Разумный предел: больше — уже не кубик, а генератор.
MAX_SPAN = 100_000

#: Варианты подачи. Короткие и без барной темы: число — не повод наливать.
PHRASINGS = (
    "{n} — выпало честно, кубик у меня свой.",
    "Выпало {n}.",
    "{n}. Случайность — единственное, что я не предсказываю.",
    "{n} — как в жизни: без плана.",
    "{n}. Если не нравится — переброшу, но честнее не станет.",
)


def parse_range(text: str) -> tuple[int, int] | None:
    """Диапазон из просьбы или None, если это не просьба о числе.

    Возвращает (низ, верх) включительно. Мусорные и вывернутые диапазоны
    отбрасываются: на них пусть отвечает модель, а не кубик.
    """
    if not RANDOM_NUMBER.search(text):
        return None
    match = RANGE.search(text)
    if match is None:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    if low > high:
        low, high = high, low
    if high - low + 1 > MAX_SPAN:
        return None
    return low, high


def draw(low: int, high: int, *, rng: random.Random | None = None) -> int:
    """Честное число из диапазона включительно."""
    generator = rng or random.SystemRandom()
    return generator.randint(low, high)


def compose(number: int, *, rng: random.Random | None = None) -> str:
    """Подача выпавшего числа в характере.

    Вариант выбирается тоже случайно, чтобы ответ не стал новым шаблоном.
    """
    generator = rng or random.SystemRandom()
    template = generator.choice(PHRASINGS)
    return template.format(n=number)


def answer(text: str, *, rng: random.Random | None = None) -> str | None:
    """Готовый ответ, если гость просил случайное число, иначе None."""
    bounds = parse_range(text)
    if bounds is None:
        return None
    low, high = bounds
    generator = rng or random.SystemRandom()
    return compose(draw(low, high, rng=generator), rng=generator)
