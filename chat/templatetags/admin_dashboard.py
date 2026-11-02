"""Живые показатели и карточки разделов для главной страницы админки."""

from datetime import timedelta

from django import template
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import capfirst

register = template.Library()

#: Разделы в том порядке, в котором с ними работают.
SECTIONS = (
    (
        "Модерация",
        (
            "chat.MessageReport",
            "chat.ModerationEvent",
            "chat.Message",
        ),
    ),
    (
        "Беседа",
        (
            "chat.Room",
            "chat.Attachment",
            "chat.MessageReaction",
        ),
    ),
    (
        "Птица и Семён",
        (
            "chat.BirdSettings",
            "chat.BirdPost",
            "chat.BartenderJob",
        ),
    ),
    (
        "Люди",
        (
            "users.User",
            "users.UserReport",
            "users.ChatStatus",
            "users.PushSubscription",
        ),
    ),
)


def _section_models(labels):
    cards = []
    for label in labels:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        model_admin = admin.site._registry.get(model)
        if model_admin is None:
            continue
        try:
            url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        except NoReverseMatch:
            continue
        cards.append(
            {
                "name": capfirst(model._meta.verbose_name_plural),
                "url": url,
            }
        )
    return cards


@register.inclusion_tag("admin/_dashboard_metrics.html")
def dashboard_metrics():
    from chat.models import BirdPost, Message, MessageReport
    from chat.services.bird import load_config

    day_ago = timezone.now() - timedelta(days=1)
    bird = load_config()

    # Миграции могут ещё не примениться (деплой): сводка не должна ронять
    # главную страницу админки.
    try:
        last_post = BirdPost.objects.order_by("-created_at").first()
        messages_today = Message.objects.filter(created_at__gte=day_ago).count()
        hidden = Message.objects.filter(hidden_at__isnull=False).count()
        open_reports = MessageReport.objects.filter(outcome="").count()
        new_people = (
            get_user_model().objects.filter(date_joined__gte=day_ago).count()
        )
    except DatabaseError:
        return {"metrics": [], "sections": [], "bird": {"enabled": False}}

    metrics = [
        {
            "label": "Сообщений за сутки",
            "value": messages_today,
            "url": reverse("admin:chat_message_changelist"),
        },
        {
            "label": "Скрыто сообщений",
            "value": hidden,
            "url": reverse("admin:chat_message_changelist"),
        },
        {
            "label": "Открытых жалоб",
            "value": open_reports,
            "url": reverse("admin:chat_messagereport_changelist"),
        },
        {
            "label": "Людей за сутки",
            "value": new_people,
            "url": reverse("admin:users_user_changelist"),
        },
    ]

    sections = [
        {"title": title, "models": _section_models(labels)}
        for title, labels in SECTIONS
    ]

    return {
        "metrics": metrics,
        "sections": [section for section in sections if section["models"]],
        "bird": {
            "enabled": bird.enabled,
            "room": bird.room_slug,
            "last": last_post.created_at if last_post else None,
            "url": reverse("admin:chat_birdsettings_changelist"),
        },
    }
