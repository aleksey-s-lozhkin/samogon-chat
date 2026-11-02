"""Птица-Говорун: когда молчит, что говорит и кому это видно.

Проверки делятся на три части: решение («стоит ли говорить»), публикация
(«что именно происходит в чате») и границы с персонажами (птицу нельзя
пригласить, ей нельзя написать лично, её не считают гостем). Бармен — отдельный
случай: личный вопрос ему идёт своим путём, и это проверяется отдельно.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from chat.models import BartenderJob, BirdPost, Message, Room
from chat.services import bird
from chat.services.bartender_context import build_bartender_conversation
from chat.services.guests import eligible_guests
from chat.services.messages import MessageService
from chat.services.ollama import OllamaUnavailable


User = get_user_model()

BIRD_SETTINGS = dict(
    BIRD_ENABLED=True,
    BIRD_ROOM_SLUG="u-stoyki",
    BIRD_USERNAME="govorun",
    BIRD_INTERVAL_MINUTES=30,
    BIRD_DAILY_LIMIT=12,
    BIRD_QUIET_HOURS="1-8",
    BIRD_QUIET_TIMEZONE="Europe/Moscow",
    BIRD_MAX_LENGTH=220,
    BIRD_TOPIC_HISTORY=20,
)


class BirdDecisionTests(TestCase):
    def setUp(self):
        # Публичные комнаты создаются миграцией, поэтому берём готовую.
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.owner = User.objects.create_user(username="owner", is_superuser=True)
        self.guest = User.objects.create_user(username="guest")
        # Полдень по Москве: тихие часы не мешают.
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)
        self.night = datetime(2026, 11, 2, 0, 30, tzinfo=dt_timezone.utc)

    @override_settings(BIRD_ENABLED=False)
    def test_disabled_by_default(self):
        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("переключател", decision.reason)

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ROOM_SLUG": ""})
    def test_requires_a_configured_room(self):
        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("беседа", decision.reason)

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ROOM_SLUG": "nowhere"})
    def test_missing_room_is_reported(self):
        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("не найдена", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_silent_when_a_guest_is_present(self):
        decision = bird.decide(now=self.daytime, room_users=["guest"])

        self.assertFalse(decision.speak)
        self.assertIn("гости", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_owner_and_characters_are_not_guests(self):
        """Хозяин и персонажи не выключают птицу: иначе он не увидит разговор."""
        decision = bird.decide(
            now=self.daytime,
            room_users=["owner", "semen", "govorun"],
        )

        self.assertTrue(decision.speak, decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_quiet_hours_silence_the_bird(self):
        decision = bird.decide(now=self.night, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("тихий час", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_respects_the_interval(self):
        message = Message.objects.create(user=self.owner, room=self.room, text="привет")
        post = BirdPost.objects.create(room=self.room, message=message, topic_id="x")
        BirdPost.objects.filter(pk=post.pk).update(created_at=self.daytime - timedelta(minutes=5))

        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("рано", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_speaks_again_after_the_interval(self):
        message = Message.objects.create(user=self.owner, room=self.room, text="привет")
        post = BirdPost.objects.create(room=self.room, message=message, topic_id="x")
        BirdPost.objects.filter(pk=post.pk).update(created_at=self.daytime - timedelta(minutes=40))

        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertTrue(decision.speak, decision.reason)

    @override_settings(**{**BIRD_SETTINGS, "BIRD_DAILY_LIMIT": 2})
    def test_daily_limit_stops_the_chatter(self):
        for index in range(2):
            message = Message.objects.create(
                user=self.owner,
                room=self.room,
                text=f"сообщение {index}",
            )
            post = BirdPost.objects.create(
                room=self.room,
                message=message,
                topic_id=f"topic-{index}",
            )
            BirdPost.objects.filter(pk=post.pk).update(created_at=self.daytime - timedelta(hours=2))

        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("потолок", decision.reason)


class BirdSpeakTests(TestCase):
    def setUp(self):
        # Публичные комнаты создаются миграцией, поэтому берём готовую.
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.owner = User.objects.create_user(username="owner", is_superuser=True)
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    def say(self, text):
        return lambda payload: text

    @override_settings(**BIRD_SETTINGS)
    def test_publishes_message_and_asks_the_bartender(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=self.say("Говорят, первый диск весил тонну."),
        )

        self.assertEqual(text, "Говорят, первый диск весил тонну.")
        message = Message.objects.get(room=self.room)
        self.assertEqual(message.text, text)
        self.assertEqual(message.user.username, "govorun")
        self.assertEqual(BirdPost.objects.get().message, message)
        # Задание Семёну создано ровно одно, без дублей.
        self.assertEqual(BartenderJob.objects.filter(question=message).count(), 1)

    @override_settings(**BIRD_SETTINGS)
    def test_dry_run_writes_nothing(self):
        text = bird.speak(
            dry_run=True,
            now=self.daytime,
            room_users=[],
            ask=self.say("Черновик реплики."),
        )

        self.assertEqual(text, "Черновик реплики.")
        self.assertFalse(Message.objects.exists())
        self.assertFalse(BirdPost.objects.exists())
        self.assertFalse(BartenderJob.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_force_bypasses_pauses(self):
        text = bird.speak(
            force=True,
            now=self.daytime,
            room_users=["guest"],
            ask=self.say("Напролом."),
        )

        self.assertEqual(text, "Напролом.")
        self.assertTrue(Message.objects.exists())

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ENABLED": False})
    def test_force_does_not_override_the_switch(self):
        text = bird.speak(force=True, now=self.daytime, room_users=[], ask=self.say("Нельзя."))

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_model_failure_stays_silent(self):
        def boom(payload):
            raise OllamaUnavailable

        text = bird.speak(now=self.daytime, room_users=[], ask=boom)

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_retries_when_the_model_mixes_languages(self):
        replies = iter(["Проверь порт сервера。", "Проверь порт сервера."])

        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: next(replies),
        )

        self.assertEqual(text, "Проверь порт сервера.")

    @override_settings(**BIRD_SETTINGS)
    def test_gives_up_after_two_bad_replies(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Проверь порт сервера。",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**{**BIRD_SETTINGS, "BIRD_MAX_LENGTH": 20})
    def test_rejects_overlong_reply(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=self.say("Очень длинная реплика, которая явно не влезает в предел."),
        )

        self.assertIsNone(text)

    @override_settings(**BIRD_SETTINGS)
    def test_avoids_repeating_recent_topics(self):
        class First:
            @staticmethod
            def choice(pool):
                return pool[0]

        topics = bird.load_topics()
        self.assertGreater(len(topics), 5)
        for topic in topics[:5]:
            message = Message.objects.create(user=self.owner, room=self.room, text="x")
            BirdPost.objects.create(room=self.room, message=message, topic_id=topic["id"])

        picked = bird.pick_topic(room=self.room, rng=First())

        self.assertNotIn(picked["id"], {topic["id"] for topic in topics[:5]})


class BirdCharacterTests(TestCase):
    """Границы с персонажами: птица — не гость и не собеседник для личек."""

    def setUp(self):
        # Публичные комнаты создаются миграцией, поэтому берём готовую.
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.guest = User.objects.create_user(username="guest")
        self.bird_user = bird.get_bird_user()

    def test_bird_is_not_in_the_guest_list(self):
        names = {user.username for user in eligible_guests()}

        self.assertNotIn("govorun", names)
        self.assertIn("guest", names)

    def test_bird_has_a_human_facing_name(self):
        self.assertEqual(
            MessageService.display_username("govorun"),
            "Птица-Говорун",
        )

    def test_bartender_sees_the_bird_by_name(self):
        Message.objects.create(user=self.guest, room=self.room, text="Тоже привет")
        Message.objects.create(user=self.bird_user, room=self.room, text="Я тут уже чирикала")
        question = Message.objects.create(user=self.bird_user, room=self.room, text="Привет, Семён")

        conversation = build_bartender_conversation(
            room=self.room,
            question=question,
            user_id=self.bird_user.pk,
        )

        self.assertEqual(conversation.current_author, "govorun")
        authors = {entry.author for entry in conversation.history}
        self.assertIn("govorun", authors)
        # Гость остаётся псевдонимом: настоящие имена персонажу не нужны.
        self.assertIn("guest_1", authors)
        self.assertNotIn("guest", authors)

    def test_bird_cannot_be_invited_to_a_private_room(self):
        from chat.services.private_rooms import RoomOperationError, create_private_room

        # Список приглашаемых строится из eligible_guests(), где персонажей нет.
        with self.assertRaises(RoomOperationError):
            create_private_room(
                actor=self.guest,
                data={"name": "Тихая", "members": [self.bird_user.pk]},
            )


class BirdCommandTests(TestCase):
    """Команда — то, что запускает cron, поэтому проверяем её вывод."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )

    @override_settings(BIRD_ENABLED=False)
    def test_reports_why_it_stays_silent(self):
        output = StringIO()

        call_command("bird_chatter", stdout=output)

        self.assertIn("Молчит", output.getvalue())
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.send_chat", return_value="Говорят, диск весил тонну.")
    def test_dry_run_shows_the_line_without_writing(self, send_chat):
        output = StringIO()

        call_command("bird_chatter", "--force", "--dry-run", stdout=output)

        self.assertIn("Сказала бы", output.getvalue())
        self.assertIn("диск весил тонну", output.getvalue())
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.send_chat", return_value="Говорят, диск весил тонну.")
    def test_force_speaks(self, send_chat):
        output = StringIO()

        call_command("bird_chatter", "--force", stdout=output)

        self.assertIn("Сказала:", output.getvalue())
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)
        self.assertEqual(BirdPost.objects.count(), 1)


class BirdRhythmTests(TestCase):
    """Ритм проверяется дважды: до генерации и под блокировкой.

    Настоящее пересечение двух процессов на SQLite не воспроизвести
    (`select_for_update` там пустой), поэтому проверяется то, что можно
    проверить честно: вторая проверка под блокировкой видит реплику, даже если
    решение принималось по устаревшим данным.
    """

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.owner = User.objects.create_user(username="owner", is_superuser=True)
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    def test_second_run_right_after_the_first_stays_silent(self):
        first = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Первая реплика.",
        )
        # Время реплики ставим ровно в «сейчас» теста: иначе результат зависел
        # бы от реальных часов машины.
        BirdPost.objects.update(created_at=self.daytime)

        second = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Вторая реплика.",
        )

        self.assertEqual(first, "Первая реплика.")
        self.assertIsNone(second)
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)

    @override_settings(**BIRD_SETTINGS)
    def test_speaks_again_when_the_interval_has_passed(self):
        first = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Первая реплика.",
        )
        BirdPost.objects.update(created_at=self.daytime - timedelta(minutes=40))

        second = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Вторая реплика.",
        )

        self.assertEqual(first, "Первая реплика.")
        self.assertEqual(second, "Вторая реплика.")
        self.assertEqual(Message.objects.filter(room=self.room).count(), 2)


class BirdSafetyTests(TestCase):
    """Дефекты, найденные на ревью: чужой аккаунт, слепое присутствие, личка."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.owner = User.objects.create_user(username="owner", is_superuser=True)
        self.guest = User.objects.create_user(username="guest")
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    def test_unknown_presence_keeps_the_bird_silent(self):
        """«Не знаю, есть ли гости» — это не «гостей нет»."""
        decision = bird.decide(now=self.daytime, room_users=None)

        self.assertFalse(decision.speak)
        self.assertIn("присутствие", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    def test_unknown_presence_stops_publication(self):
        text = bird.speak(
            now=self.daytime,
            room_users=None,
            ask=lambda payload: "Нельзя говорить.",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_refuses_a_username_owned_by_a_live_account(self):
        """Иначе после смены имени птица заговорила бы от чужого лица."""
        User.objects.create_user(username="govorun", password="known-pass")

        with self.assertRaises(bird.BirdIdentityError):
            bird.get_bird_user()

        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "От чужого лица.",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**{**BIRD_SETTINGS, "BIRD_USERNAME": "semen"})
    def test_refuses_to_share_the_bartender_username(self):
        with self.assertRaises(bird.BirdIdentityError):
            bird.get_bird_user()

    @override_settings(**BIRD_SETTINGS)
    def test_bird_account_is_inactive_and_passwordless(self):
        bird_user = bird.get_bird_user()

        self.assertFalse(bird_user.is_active)
        self.assertFalse(bird_user.has_usable_password())

    @override_settings(**BIRD_SETTINGS)
    def test_guest_cannot_write_to_the_bird_directly(self):
        from users.services.recipients import resolve_recipient

        bird.get_bird_user()
        other = User.objects.create_user(username="other-guest")

        self.assertIsNone(
            resolve_recipient(username="govorun", viewer=self.guest, room=self.room)
        )
        # Бармен — исключение: личный вопрос ему это функция, а не дыра.
        bartender = User.objects.create_user(username="semen")
        self.assertEqual(
            resolve_recipient(username="semen", viewer=self.guest, room=self.room),
            bartender,
        )
        # Обычный гость по-прежнему доступен: проверка не запрещает всё подряд.
        self.assertEqual(
            resolve_recipient(username="other-guest", viewer=self.guest, room=self.room),
            other,
        )

    @override_settings(**BIRD_SETTINGS)
    def test_attachment_form_rejects_a_bot_recipient(self):
        """Через форму с вложением нельзя написать даже активному персонажу."""
        from django.urls import reverse

        bird_user = bird.get_bird_user()
        # Имитируем сценарий из ревью: аккаунт персонажа активен.
        User.objects.filter(pk=bird_user.pk).update(is_active=True)
        self.client.force_login(self.guest)

        response = self.client.post(
            reverse("chat:create_attachment_message", args=[self.room.slug]),
            {"recipient": "govorun", "text": "Привет, птица"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Message.objects.filter(recipient_id=bird_user.pk).exists())

    @override_settings(**BIRD_SETTINGS)
    def test_recipient_list_hides_an_active_character(self):
        from chat.selectors import get_message_recipients

        bird_user = bird.get_bird_user()
        User.objects.filter(pk=bird_user.pk).update(is_active=True)

        names = set(
            get_message_recipients(
                room=self.room,
                viewer=self.owner,
            ).values_list("username", flat=True)
        )

        self.assertNotIn("govorun", names)
        self.assertIn("guest", names)


class BirdSettingsTests(TestCase):
    """Опечатка в настройке не должна незаметно менять режим."""

    @override_settings(BIRD_QUIET_HOURS="8-25")
    def test_out_of_range_window_is_ignored(self):
        self.assertEqual(bird.quiet_window(), (0, 0))

    @override_settings(BIRD_QUIET_HOURS="1")
    def test_window_without_dash_is_ignored(self):
        self.assertEqual(bird.quiet_window(), (0, 0))

    @override_settings(BIRD_QUIET_HOURS="22-9")
    def test_window_across_midnight_is_kept(self):
        self.assertEqual(bird.quiet_window(), (22, 9))

    def test_topics_survive_a_json_array_instead_of_an_object(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topics.json"
            path.write_text(
                json.dumps([{"id": "a", "fact": "факт"}, {"id": "b", "fact": None}]),
                encoding="utf-8",
            )
            with patch.object(bird, "TOPICS_PATH", path):
                topics = bird.load_topics()

        self.assertEqual([topic["id"] for topic in topics], ["a"])


class BirdLanguageRetryTests(TestCase):
    """Повторная попытка должна переписывать ответ, а не повторять промпт."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    def test_retry_passes_the_previous_answer_to_the_model(self):
        seen = []

        def ask(payload):
            seen.append(payload)
            if len(seen) == 1:
                return "Проверь порт сервера。"
            return "Проверь порт сервера."

        text = bird.speak(now=self.daytime, room_users=[], ask=ask)

        self.assertEqual(text, "Проверь порт сервера.")
        self.assertEqual(len(seen), 2)
        retry_prompt = seen[1]["messages"][-1]["content"]
        self.assertIn("Проверь порт сервера", retry_prompt)
        self.assertNotEqual(seen[0]["messages"], seen[1]["messages"])


class BirdContentTests(TestCase):
    """Ограничения тем держатся не только на просьбе в промпте."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    def test_drink_talk_is_refused(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Пойдём выпьем водки, Семён!",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_politics_is_refused(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "А вот про политику я скажу так.",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    def test_ordinary_line_passes_the_filter(self):
        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Говорят, первый диск весил тонну.",
        )

        self.assertEqual(text, "Говорят, первый диск весил тонну.")


class BirdScheduledPathTests(TestCase):
    """Штатный путь без --force: именно его запускает cron.

    Раньше эти тесты отсутствовали, и ошибка «по умолчанию передаётся None,
    то есть неизвестно» оставалась незамеченной: все тесты передавали
    присутствие явно, а команда проверялась только с --force.
    """

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.room_users_sync", return_value=[])
    def test_decision_asks_for_presence_when_not_given(self, presence):
        decision = bird.decide(now=self.daytime)

        self.assertTrue(decision.speak, decision.reason)
        presence.assert_called()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.room_users_sync", return_value=["guest"])
    def test_decision_sees_guests_through_the_same_path(self, presence):
        User.objects.create_user(username="guest")

        decision = bird.decide(now=self.daytime)

        self.assertFalse(decision.speak)
        self.assertIn("гости", decision.reason)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.send_chat", return_value="Говорят, диск весил тонну.")
    @patch("chat.services.bird.room_users_sync", return_value=[])
    def test_command_speaks_without_force(self, presence, send_chat):
        """Команда по расписанию обязана говорить, а не молчать всегда."""
        output = StringIO()

        call_command("bird_chatter", stdout=output)

        self.assertIn("Сказала:", output.getvalue())
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.send_chat", return_value="Говорят, диск весил тонну.")
    @patch("chat.services.bird.room_users_sync", return_value=None)
    def test_command_stays_silent_when_presence_is_unknown(self, presence, send_chat):
        """Redis не ответил — команда молчит, а не говорит наугад."""
        output = StringIO()

        call_command("bird_chatter", stdout=output)

        self.assertIn("Молчит", output.getvalue())
        self.assertIn("присутствие", output.getvalue())
        self.assertFalse(Message.objects.exists())
        send_chat.assert_not_called()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.send_chat", return_value="Говорят, диск весил тонну.")
    @patch("chat.services.bird.room_users_sync", return_value=["guest"])
    def test_command_stays_silent_when_a_guest_is_present(self, presence, send_chat):
        User.objects.create_user(username="guest")
        output = StringIO()

        call_command("bird_chatter", stdout=output)

        self.assertIn("Молчит", output.getvalue())
        self.assertIn("гости", output.getvalue())
        send_chat.assert_not_called()


class BirdLateCheckTests(TestCase):
    """Гость, зашедший во время генерации, отменяет реплику."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.room_users_sync", side_effect=[[], ["guest"]])
    def test_guest_arriving_during_generation_cancels_the_line(self, presence):
        """Первая проверка видит пустую комнату, вторая — гостя."""
        text = bird.speak(
            now=self.daytime,
            ask=lambda payload: "Реплика, которой не суждено выйти.",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())
        self.assertGreaterEqual(presence.call_count, 2)

    @override_settings(**{**BIRD_SETTINGS, "BIRD_QUIET_HOURS": "1-23"})
    @patch("chat.services.bird.room_users_sync", side_effect=[[], []])
    def test_quiet_hours_starting_during_generation_cancel_the_line(self, presence):
        """Тихий час ловится на второй проверке, даже если решали днём."""
        text = bird.speak(
            now=self.daytime,
            ask=lambda payload: "Тоже не выйдет.",
        )

        self.assertIsNone(text)
        self.assertFalse(Message.objects.exists())


class OllamaTransportTests(TestCase):
    """Настоящие отказы транспорта, а не заранее собранное исключение."""

    @staticmethod
    def payload():
        from chat.services.ollama import chat_payload

        return chat_payload([{"role": "user", "content": "x"}], temperature=0.5, num_predict=10)

    def test_broken_status_line_becomes_unavailable(self):
        from http.client import BadStatusLine

        from chat.services.ollama import OllamaUnavailable, send_chat

        with patch("chat.services.ollama.urlopen", side_effect=BadStatusLine("x")):
            with self.assertRaises(OllamaUnavailable):
                send_chat(self.payload())

    def test_broken_json_becomes_unavailable(self):
        from chat.services.ollama import OllamaUnavailable, send_chat

        with patch("chat.services.ollama.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = b"not json"
            with self.assertRaises(OllamaUnavailable):
                send_chat(self.payload())

    def test_unexpected_payload_shape_becomes_unavailable(self):
        from chat.services.ollama import OllamaUnavailable, send_chat

        with patch("chat.services.ollama.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = b'{"message": "text"}'
            with self.assertRaises(OllamaUnavailable):
                send_chat(self.payload())

    def test_bad_base_url_becomes_unavailable(self):
        from chat.services.ollama import OllamaUnavailable, send_chat

        with override_settings(OLLAMA_BASE_URL="not a url"):
            with self.assertRaises(OllamaUnavailable):
                send_chat(self.payload())


class BirdPublishFailureTests(TestCase):
    """Сбой рассылки не должен отменять уже принятую реплику."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.broadcast_message", side_effect=RuntimeError("redis down"))
    def test_message_and_job_survive_a_broadcast_failure(self, broadcast):
        from chat.models import BartenderJob

        text = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Сказано, но не разослано.",
        )

        self.assertEqual(text, "Сказано, но не разослано.")
        message = Message.objects.get(room=self.room)
        self.assertEqual(BartenderJob.objects.filter(question=message).count(), 1)
        self.assertEqual(BirdPost.objects.count(), 1)


class PresenceDeduplicationTests(TestCase):
    """Две вкладки одного гостя — это один гость."""

    @override_settings(**BIRD_SETTINGS)
    def test_same_guest_twice_is_counted_once(self):
        guests = bird.guests_in_room("u-stoyki", room_users=["guest", "guest"])

        self.assertEqual(guests, ["guest"])


class BartenderAccessTests(TestCase):
    """Граница бармена закреплена так, как она есть в реальной конфигурации.

    Раньше это проверялось только тестом с вручную активированным `semen`,
    из-за чего можно было решить, что бармен есть в списке получателей.
    """

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.guest = User.objects.create_user(username="guest")

    def test_real_bartender_is_not_a_generic_recipient(self):
        from chat.selectors import get_message_recipients
        from chat.services.bartender import bartender
        from users.services.recipients import resolve_recipient

        semen = bartender.get_bartender_user()

        # Аккаунт технический и неактивный: под ним нельзя войти.
        self.assertFalse(semen.is_active)
        self.assertFalse(semen.has_usable_password())

        names = set(
            get_message_recipients(room=self.room, viewer=self.guest).values_list(
                "username", flat=True
            )
        )
        self.assertNotIn("semen", names)
        self.assertIsNone(
            resolve_recipient(username="semen", viewer=self.guest, room=self.room)
        )

    def test_active_bartender_would_stay_reachable(self):
        """Если бармена когда-нибудь сделают активным, он не станет ботом-заглушкой.

        Именно ради этого случая персонажи разделены на два набора: «можно
        написать» и «нельзя». Сегодня его отсекает неактивность, но правило
        должно оставаться верным и без неё.
        """
        from chat.selectors import get_message_recipients
        from users.services.recipients import resolve_recipient

        semen = User.objects.create_user(username="semen", is_active=True)

        self.assertEqual(
            resolve_recipient(username="semen", viewer=self.guest, room=self.room),
            semen,
        )
        names = set(
            get_message_recipients(room=self.room, viewer=self.guest).values_list(
                "username", flat=True
            )
        )
        self.assertIn("semen", names)

    def test_bird_stays_unreachable_even_when_active(self):
        """Обратная сторона правила: активация не делает птицу собеседником."""
        from users.services.recipients import resolve_recipient

        self.assertEqual(
            User.objects.filter(username="govorun").update(is_active=True),
            0,
        )
        bird_user = bird.get_bird_user()
        User.objects.filter(pk=bird_user.pk).update(is_active=True)

        self.assertIsNone(
            resolve_recipient(username="govorun", viewer=self.guest, room=self.room)
        )


class BirdSchedulingTests(TestCase):
    """Расписание живёт внутри воркера: без cron и без Celery beat."""

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.tasks.bird_tick.apply_async")
    def test_schedule_arms_the_next_tick(self, apply_async):
        from chat.tasks import schedule_bird_tick

        self.assertTrue(schedule_bird_tick())
        apply_async.assert_called_once_with(
            countdown=settings.BIRD_CHECK_SECONDS,
        )

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ENABLED": False})
    @patch("chat.tasks.bird_tick.apply_async")
    def test_disabled_bird_does_not_arm_anything(self, apply_async):
        from chat.tasks import schedule_bird_tick

        self.assertFalse(schedule_bird_tick())
        apply_async.assert_not_called()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.tasks.schedule_bird_tick")
    @patch("chat.services.bird.speak", side_effect=RuntimeError("boom"))
    def test_tick_survives_a_failure_and_rearms(self, speak, schedule):
        from chat.tasks import bird_tick

        # Исключение не должно выходить наружу, иначе цепочка оборвётся.
        bird_tick.run()

        speak.assert_called_once_with()
        schedule.assert_called_once_with()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.tasks.schedule_bird_tick")
    @patch("chat.services.bird.speak")
    def test_tick_speaks_and_rearms(self, speak, schedule):
        from chat.tasks import bird_tick

        bird_tick.run()

        speak.assert_called_once_with()
        schedule.assert_called_once_with()


class BirdAdminTests(TestCase):
    """Переключатель в админке: включение без перезапуска и обкатка кнопкой."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pass",
        )
        self.client.force_login(self.admin)
        self.change_url = reverse("admin:chat_birdsettings_change", args=[1])
        self.changelist_url = reverse("admin:chat_birdsettings_changelist")

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ENABLED": False})
    def test_admin_row_overrides_environment(self):
        """Строка в админке главнее окружения: включили — значит включена."""
        from chat.models import BirdSettings
        from chat.services.bird import load_config

        BirdSettings.objects.create(
            enabled=True,
            room=self.room,
            interval_minutes=15,
            daily_limit=3,
        )

        config = load_config()

        self.assertTrue(config.enabled)
        self.assertEqual(config.room_slug, "u-stoyki")
        self.assertEqual(config.interval_minutes, 15)
        self.assertEqual(config.daily_limit, 3)

    @override_settings(**BIRD_SETTINGS)
    def test_without_a_row_environment_values_apply(self):
        from chat.services.bird import load_config

        config = load_config()

        self.assertEqual(config.enabled, settings.BIRD_ENABLED)
        self.assertEqual(config.room_slug, settings.BIRD_ROOM_SLUG)

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.tasks.schedule_bird_tick", return_value=True)
    def test_saving_enabled_arms_the_tick_immediately(self, schedule):
        """Иначе включение ждало бы перезапуска воркера."""
        from chat.models import BirdSettings

        BirdSettings.objects.create(enabled=False, room=self.room)
        response = self.client.post(
            self.change_url,
            {
                "enabled": "on",
                "room": str(self.room.pk),
                "interval_minutes": "30",
                "daily_limit": "12",
                "quiet_from": "1",
                "quiet_to": "8",
                "timezone_name": "Europe/Moscow",
            },
        )

        self.assertEqual(response.status_code, 302)
        schedule.assert_called_once()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.tasks.schedule_bird_tick", return_value=True)
    def test_saving_disabled_does_not_arm_anything(self, schedule):
        from chat.models import BirdSettings

        BirdSettings.objects.create(enabled=False, room=self.room)
        response = self.client.post(
            self.change_url,
            {
                "room": str(self.room.pk),
                "interval_minutes": "30",
                "daily_limit": "12",
                "quiet_from": "1",
                "quiet_to": "8",
                "timezone_name": "Europe/Moscow",
            },
        )

        self.assertEqual(response.status_code, 302)
        schedule.assert_not_called()

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.speak", return_value="Говорят, диск весил тонну.")
    def test_draft_action_shows_the_line_without_posting(self, speak):
        from chat.models import BirdSettings

        BirdSettings.objects.create(enabled=True, room=self.room)
        response = self.client.post(
            self.changelist_url,
            {
                "action": "draft_now",
                "_selected_action": ["1"],
            },
            follow=True,
        )

        self.assertContains(response, "Сказала бы")
        speak.assert_called_once()
        self.assertTrue(speak.call_args.kwargs["dry_run"])
        self.assertFalse(Message.objects.exists())

    @override_settings(**BIRD_SETTINGS)
    @patch("chat.services.bird.speak", return_value="Сказано по-настоящему.")
    def test_say_action_reports_failure_honestly(self, speak):
        from chat.models import BirdSettings

        BirdSettings.objects.create(enabled=True, room=self.room)
        speak.return_value = None

        response = self.client.post(
            self.changelist_url,
            {"action": "say_now", "_selected_action": ["1"]},
            follow=True,
        )

        self.assertContains(response, "Сказать не удалось")

    @override_settings(**BIRD_SETTINGS)
    def test_only_one_settings_row_can_exist(self):
        from chat.models import BirdSettings

        first = BirdSettings.objects.create(enabled=False)
        second = BirdSettings.objects.create(enabled=True)

        self.assertEqual(first.pk, 1)
        self.assertEqual(second.pk, 1)
        self.assertEqual(BirdSettings.objects.count(), 1)
        self.assertTrue(BirdSettings.objects.get().enabled)
