"""Птица-Говорун: когда молчит, что говорит и кому это видно.

Проверки делятся на три части: решение («стоит ли говорить»), публикация
(«что именно происходит в чате») и границы с персонажами (птицу нельзя
пригласить, ей нельзя написать лично, её не считают гостем).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

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
        self.assertIn("BIRD_ENABLED", decision.reason)

    @override_settings(**{**BIRD_SETTINGS, "BIRD_ROOM_SLUG": ""})
    def test_requires_a_configured_room(self):
        decision = bird.decide(now=self.daytime, room_users=[])

        self.assertFalse(decision.speak)
        self.assertIn("BIRD_ROOM_SLUG", decision.reason)

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


class BirdConcurrencyTests(TestCase):
    """Пересечение двух запусков не должно дать две реплики."""

    def setUp(self):
        self.room, _ = Room.objects.get_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        self.owner = User.objects.create_user(username="owner", is_superuser=True)
        self.daytime = datetime(2026, 11, 2, 9, 0, tzinfo=dt_timezone.utc)

    @override_settings(**BIRD_SETTINGS)
    def test_second_run_right_after_the_first_stays_silent(self):
        """Первый запуск сказал — второй в тот же момент молчит."""
        first = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Первая реплика.",
        )
        second = bird.speak(
            now=self.daytime,
            room_users=[],
            ask=lambda payload: "Вторая реплика.",
        )

        self.assertEqual(first, "Первая реплика.")
        self.assertIsNone(second)
        self.assertEqual(Message.objects.filter(room=self.room).count(), 1)
