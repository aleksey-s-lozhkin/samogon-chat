"""Договор с общей моделью: Семён и лапоть на одной видеокарте.

Семён и лапоть работают на одной машине и **на одной загруженной модели**.
Это выгодно обоим — никто никого не вытесняет, — но держится на
условиях, каждое из которых легко нарушить незаметно.

1. **Базовая модель, а не пресет.** Пресет в Ollama — отдельная модель и
   отдельная загрузка. Замерено: загрузка `samogon-semen-*` вытесняет
   общую модель, хотя собрана из тех же весов. Цена — около двадцати
   секунд на каждое переключение, и заметно это только по задержкам.

2. **Одинаковый `num_ctx`.** Параметр загрузки, а не запроса. Разные
   значения заставляют Ollama перезагружать модель при каждом переходе
   между приложениями: замерено 4,2 секунды в каждую сторону.

3. **Свой характер, и только свой.** Характер Семёна живёт в
   `chat/services/prompts/semen.txt` и уходит системным сообщением. Он не
   должен попадать ни в Modelfile, ни в другое приложение.

4. **Таймаут с запасом на холодную загрузку.** Двадцать секунд равнялись
   времени загрузки модели, и запрос отдавал гостю заготовку вместо
   ответа.

Тесты дешёвые, а поломка дорогая и тихая: всё продолжит работать, просто
в разы медленнее и с заготовками вместо ответов.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Как называется общая модель. Обе программы обязаны звать её одинаково.
SHARED_MODEL = "qwen3:8b"

#: Согласованный размер контекста. Лапоть держит то же значение;
#: расхождение даёт перезагрузку на каждом переключении.
SHARED_NUM_CTX = 8192

#: Как называются собственные модели Семёна. Они остаются в Ollama, но
#: приложением больше не используются.
PRESET_PREFIX = "samogon-"

PERSONA_PATH = Path(__file__).with_name("services") / "prompts" / "semen.txt"
BARTENDER_PATH = Path(__file__).with_name("services") / "bartender.py"


class TestSharedModelContract(SimpleTestCase):
    """Условия совместной работы с лаптем."""

    def test_model_is_the_base_one(self) -> None:
        """Приложение зовёт базовую модель, а не собственный пресет.

        Пресет — отдельная модель для Ollama. Её загрузка вытесняет общую,
        и оба приложения начинают перезагружать модель друг за другом.
        """
        self.assertFalse(
            settings.OLLAMA_MODEL.startswith(PRESET_PREFIX),
            f"OLLAMA_MODEL={settings.OLLAMA_MODEL!r} — это пресет: он вытеснит "
            "общую модель. Зовите базовую.",
        )

    def test_model_is_the_agreed_one(self) -> None:
        """Имя модели совпадает с согласованным."""
        self.assertEqual(settings.OLLAMA_MODEL, SHARED_MODEL)

    def test_context_matches_the_other_application(self) -> None:
        """Контекст совпадает с лаптем: иначе перезагрузки."""
        self.assertEqual(
            settings.OLLAMA_NUM_CTX,
            SHARED_NUM_CTX,
            "значение должно совпадать с OLLAMA_NUM_CTX в лапте",
        )

    def test_context_is_not_hardcoded_in_the_request(self) -> None:
        """Контекст берётся из настроек, а не из литерала в запросе.

        Зашитое число делало бы расхождение с лаптем невидимым: код
        работал бы, а перезагрузки никто бы не связал с настройкой.
        """
        source = BARTENDER_PATH.read_text(encoding="utf-8")
        self.assertIn("settings.OLLAMA_NUM_CTX", source)
        self.assertNotIn('"num_ctx": 4096', source)

    def test_timeout_leaves_room_for_a_cold_load(self) -> None:
        """Таймаут больше времени загрузки модели.

        Загрузка занимает около двадцати секунд. Таймаут в двадцать
        означал, что первый запрос после паузы гарантированно упирается в
        него, и гость получает заготовку вместо ответа.
        """
        self.assertGreaterEqual(
            settings.OLLAMA_TIMEOUT_SECONDS,
            40,
            "при холодной загрузке ~20 с таймаут обязан быть с запасом",
        )

    def test_persona_is_sent_from_the_application(self) -> None:
        """Характер лежит в файле приложения и уходит в запросе.

        Именно так его можно менять без пересборки модели и именно так он
        не попадает никуда, кроме Семёна.
        """
        self.assertTrue(PERSONA_PATH.is_file(), "файл характера пропал")
        persona = PERSONA_PATH.read_text(encoding="utf-8")
        self.assertIn("Семён", persona)
        self.assertGreater(len(persona), 1000, "характер подозрительно короткий")

    def test_persona_is_not_baked_into_the_payload(self) -> None:
        """Характер не задаётся параметром модели.

        Modelfile с SYSTEM превратил бы Семёна в отдельную модель — а это
        ровно то, от чего мы ушли.
        """
        source = BARTENDER_PATH.read_text(encoding="utf-8")
        self.assertNotIn(
            '"system":',
            source,
            "характер должен идти сообщением роли system, а не полем модели",
        )
        self.assertIn('"role": "system"', source)
