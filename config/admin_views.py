import csv
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from chat.management.commands.export_bartender_audit import redact_text
from chat.models import Attachment, BartenderJob, Message, MessageReport, Room
from users.models import User

from .admin_forms import MessageExportForm


logger = logging.getLogger(__name__)


def _activity_series(since):
    rows = {
        row["day"]: row["count"]
        for row in Message.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    }
    today = timezone.localdate()
    maximum = max(rows.values(), default=1)
    return [
        {
            "day": day,
            "count": rows.get(day, 0),
            "width": round(rows.get(day, 0) * 100 / maximum),
        }
        for day in (
            today - timedelta(days=offset)
            for offset in range(13, -1, -1)
        )
    ]


@staff_member_required
def admin_diagnostics(request):
    now = timezone.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    jobs_week = BartenderJob.objects.filter(created_at__gte=week_ago)
    messages_week = Message.objects.filter(created_at__gte=week_ago)
    top_rooms = (
        Room.objects.annotate(
            recent_messages=Count(
                "messages",
                filter=Q(messages__created_at__gte=week_ago),
            )
        )
        .order_by("-recent_messages", "name")[:6]
    )
    context = {
        **admin.site.each_context(request),
        "title": "Статистика и диагностика",
        "metrics": (
            ("Сообщений за сутки", Message.objects.filter(created_at__gte=day_ago).count()),
            ("Сообщений за 7 дней", messages_week.count()),
            ("Активных пользователей", messages_week.values("user_id").distinct().count()),
            ("Новых аккаунтов за 7 дней", User.objects.filter(date_joined__gte=week_ago).count()),
            ("Новых жалоб", MessageReport.objects.filter(resolved_at__isnull=True).count()),
            ("Файлов за 30 дней", Attachment.objects.filter(created_at__gte=month_ago).count()),
        ),
        "activity": _activity_series(now - timedelta(days=14)),
        "top_rooms": top_rooms,
        "job_counts": {
            status: jobs_week.filter(status=status).count()
            for status in BartenderJob.Status.values
        },
        "recent_failures": jobs_week.filter(
            status=BartenderJob.Status.FAILED,
        ).select_related("room", "user")[:10],
        "can_export": request.user.is_superuser,
    }
    return render(request, "admin/diagnostics.html", context)


def _export_queryset(cleaned_data):
    messages = (
        Message.objects.select_related("room", "user", "recipient")
        .prefetch_related("reactions", "attachments")
        .order_by("created_at", "id")
    )
    if cleaned_data["period"] != "all":
        messages = messages.filter(
            created_at__gte=timezone.now() - timedelta(days=int(cleaned_data["period"])),
        )
    if cleaned_data["room"]:
        messages = messages.filter(room=cleaned_data["room"])
    if not cleaned_data["include_private"]:
        messages = messages.filter(
            room__visibility=Room.Visibility.PUBLIC,
            recipient__isnull=True,
        )
    if not cleaned_data["include_hidden"]:
        messages = messages.filter(hidden_at__isnull=True)
    return messages


def _aliases(messages):
    user_ids = set(messages.values_list("user_id", flat=True))
    user_ids.update(
        user_id
        for user_id in messages.values_list("recipient_id", flat=True)
        if user_id is not None
    )
    return {
        user_id: f"user_{index:03d}"
        for index, user_id in enumerate(sorted(user_ids), start=1)
    }


def _message_record(message, aliases, *, include_content, redact_content):
    record = {
        "id": message.id,
        "created_at": message.created_at.isoformat(),
        "room": message.room.slug,
        "room_visibility": message.room.visibility,
        "author": (
            "bartender"
            if message.user.username == settings.BARTENDER_USERNAME
            else aliases[message.user_id]
        ),
        "recipient": aliases.get(message.recipient_id),
        "private": message.recipient_id is not None,
        "reply_to_id": message.reply_to_id,
        "hidden": message.hidden_at is not None,
        "text_length": len(message.text),
        "reactions": [reaction.emoji for reaction in message.reactions.all()],
        "attachment_count": len(message.attachments.all()),
    }
    if include_content:
        record["text"] = redact_text(message.text) if redact_content else message.text
    return record


def _jsonl_stream(messages, aliases, record_options, metadata):
    yield json.dumps({"record_type": "metadata", **metadata}, ensure_ascii=False) + "\n"
    for message in messages.iterator(chunk_size=500):
        record = _message_record(message, aliases, **record_options)
        yield json.dumps({"record_type": "message", **record}, ensure_ascii=False) + "\n"


class CsvEcho:
    def write(self, value):
        return value


def _csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _csv_stream(messages, aliases, record_options):
    fieldnames = [
        "id", "created_at", "room", "room_visibility", "author", "recipient",
        "private", "reply_to_id", "hidden", "text_length", "reactions",
        "attachment_count",
    ]
    if record_options["include_content"]:
        fieldnames.append("text")
    writer = csv.DictWriter(CsvEcho(), fieldnames=fieldnames)
    yield writer.writeheader()
    for message in messages.iterator(chunk_size=500):
        record = _message_record(message, aliases, **record_options)
        record["reactions"] = " ".join(record["reactions"])
        yield writer.writerow({key: _csv_safe(value) for key, value in record.items()})


@staff_member_required
def export_messages(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = MessageExportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        messages = _export_queryset(form.cleaned_data)
        message_count = messages.count()
        aliases = _aliases(messages)
        record_options = {
            "include_content": form.cleaned_data["include_content"],
            "redact_content": form.cleaned_data["redact_content"],
        }
        filename = f"samogon-messages-{timezone.now():%Y%m%dT%H%M%SZ}"
        logger.info(
            "admin_message_export user_id=%s count=%s content=%s private=%s hidden=%s format=%s",
            request.user.id,
            message_count,
            record_options["include_content"],
            form.cleaned_data["include_private"],
            form.cleaned_data["include_hidden"],
            form.cleaned_data["file_format"],
        )
        if form.cleaned_data["file_format"] == "csv":
            stream = _csv_stream(messages, aliases, record_options)
            content_type = "text/csv; charset=utf-8"
            suffix = "csv"
        else:
            stream = _jsonl_stream(
                messages,
                aliases,
                record_options,
                {
                    "exported_at": timezone.now().isoformat(),
                    "message_count": message_count,
                    "includes_content": record_options["include_content"],
                    "includes_private": form.cleaned_data["include_private"],
                    "content_redacted": record_options["redact_content"],
                },
            )
            content_type = "application/x-ndjson; charset=utf-8"
            suffix = "jsonl"
        response = StreamingHttpResponse(stream, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}.{suffix}"'
        return response

    context = {
        **admin.site.each_context(request),
        "title": "Выгрузка сообщений",
        "form": form,
    }
    return render(request, "admin/message_export.html", context)
