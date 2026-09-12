import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie

from config.rate_limit import is_allowed
from users.models import User

from .forms import MessageSearchForm, PrivateRoomForm
from .models import (
    Attachment,
    Message,
    MessageReport,
    Note,
    NoteAttachment,
    Room,
    RoomMembership,
)
from .services.attachments import (
    AttachmentInspectionUnavailable,
    AttachmentValidationError,
    create_attachment,
    create_attachments,
    normalize_audio_attachment,
    validate_attachment,
)
from .services.events import broadcast_attachment_update, broadcast_message
from .services.messages import MessageService
from .services.navigation import get_last_room_url
from .services.reports import create_message_report
from .selectors import get_published_atmosphere_lines, get_visible_rooms


def add_unread_counts(rooms, user):
    """Добавляет в объекты комнат число непрочитанных сообщений."""
    for room in rooms:
        room.unread_count = (
            MessageService.get_unread_count(room=room, user_id=user.id)
            if user.is_authenticated
            else 0
        )
    return rooms


def private_room_slug(name):
    """Создаёт уникальный URL для закрытой беседы."""
    base_slug = slugify(name) or "zakrytaya-beseda"
    slug = base_slug
    number = 2
    while Room.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"
        number += 1
    return slug


def revoke_private_room_access(*, room_slug, user_ids):
    """Закрывает активные WebSocket-вкладки бывших участников беседы."""
    channel_layer = get_channel_layer()
    for user_id in user_ids:
        async_to_sync(channel_layer.group_send)(
            f"chat_user_{user_id}",
            {"type": "room_access_revoked", "room_slug": room_slug},
        )


def rooms_page(request):
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)
    public_rooms = [room for room in rooms if not room.is_private]
    private_rooms = [room for room in rooms if room.is_private]
    owned_private_room = (
        next(
            (room for room in private_rooms if room.owner_id == request.user.id),
            None,
        )
        if request.user.is_authenticated
        else None
    )

    return render(
        request,
        "chat/rooms.html",
        {
            "rooms": public_rooms,
            "private_rooms": private_rooms,
            "owned_private_room": owned_private_room,
            "private_room_form": PrivateRoomForm(
                user=request.user,
                room=owned_private_room,
            ),
        },
    )


@ensure_csrf_cookie
def chat_page(request, room_slug):
    room = get_object_or_404(
        Room,
        slug=room_slug,
    )

    if room.is_private and (
        not request.user.is_authenticated
        or not room.memberships.filter(user=request.user).exists()
    ):
        raise Http404("Закрытая беседа не найдена")

    if request.user.is_authenticated:
        MessageService.mark_room_as_read(room=room, user_id=request.user.id)
        request.session["last_chat_room_slug"] = room.slug
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)
    private_rooms = [item for item in rooms if item.is_private]
    pending_report_count = (
        MessageReport.objects.filter(resolved_at__isnull=True).count()
        if request.user.has_perm("chat.view_messagereport")
        else 0
    )

    focus_message_id = None
    raw_focus = request.GET.get("message")
    if request.user.is_authenticated and raw_focus and raw_focus.isdigit():
        candidate = Message.objects.select_related("room", "recipient").filter(
            pk=int(raw_focus), room=room,
        ).first()
        if candidate and MessageService.can_view_message(
            message=candidate,
            user=request.user,
        ):
            focus_message_id = candidate.id

    return render(
        request,
        "chat/chat.html",
        {
            "room": room,
            "rooms": rooms,
            "public_rooms": [item for item in rooms if not item.is_private],
            "private_rooms": private_rooms,
            "owned_private_room": (
                next(
                    (
                        item
                        for item in private_rooms
                        if item.owner_id == request.user.id
                    ),
                    None,
                )
                if request.user.is_authenticated
                else None
            ),
            "focus_message_id": focus_message_id,
            "pending_report_count": pending_report_count,
            "presence_status_choices": User.PresenceStatus.choices,
            "atmosphere_lines": get_published_atmosphere_lines(),
        },
    )


@login_required
def message_search(request):
    """Ищет только среди реплик и комнат, доступных текущему пользователю."""
    form = MessageSearchForm(request.GET or None)
    results = []
    if form.is_valid():
        query = form.cleaned_data["q"]
        results = list(
            Message.objects.filter(hidden_at__isnull=True, text__icontains=query)
            .filter(
                Q(room__visibility=Room.Visibility.PUBLIC)
                | Q(room__memberships__user=request.user)
            )
            .filter(
                Q(recipient__isnull=True)
                | Q(user=request.user)
                | Q(recipient=request.user)
            )
            .select_related("room", "user", "recipient")
            .distinct()
            .order_by("-created_at")[:50]
        )
        for result in results:
            result.display_username = MessageService.display_username(
                result.user.username,
            )
    return render(
        request,
        "chat/search.html",
        {
            "form": form,
            "results": results,
            "last_room_url": get_last_room_url(request),
        },
    )


@login_required
def serve_attachment(request, attachment_id):
    """Выдаёт файл только участнику чата; в production тело отдаёт Nginx."""
    attachment = get_object_or_404(
        Attachment.objects.select_related(
            "message__room",
            "message__recipient",
        ),
        id=attachment_id,
    )
    if not MessageService.can_view_message(
        message=attachment.message,
        user=request.user,
    ):
        raise Http404("Вложение не найдено")

    as_attachment = request.path.endswith("/download/")
    if settings.DEBUG:
        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=as_attachment,
            filename=attachment.original_name,
            content_type=attachment.content_type,
        )

    response = HttpResponse(content_type=attachment.content_type)
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=as_attachment,
        filename=attachment.original_name,
    )
    response["X-Accel-Redirect"] = f"/media/{attachment.file.name}"
    return response


@login_required
def serve_note_attachment(request, attachment_id):
    """Выдаёт копию вложения только владельцу заметки."""
    attachment = get_object_or_404(
        NoteAttachment.objects.select_related("note"),
        id=attachment_id,
        note__user=request.user,
    )
    as_attachment = request.path.endswith("/download/")
    if settings.DEBUG:
        return FileResponse(
            attachment.file.open("rb"),
            as_attachment=as_attachment,
            filename=attachment.original_name,
            content_type=attachment.content_type,
        )

    response = HttpResponse(content_type=attachment.content_type)
    response["Content-Disposition"] = content_disposition_header(
        as_attachment=as_attachment,
        filename=attachment.original_name,
    )
    response["X-Accel-Redirect"] = f"/media/{attachment.file.name}"
    return response


@login_required
def add_message_attachments(request, message_id):
    """Добавляет проверенные файлы к собственному сообщению и рассылает обновление."""
    if request.method != "POST":
        raise Http404("Маршрут загрузки не найден")
    if not is_allowed(
        identifier=f"user:{request.user.id}",
        bucket="attachment",
        limit=settings.ATTACHMENT_RATE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        return JsonResponse(
            {"error": "Слишком много загрузок. Подождите минуту."},
            status=429,
        )

    message = get_object_or_404(
        Message.objects.select_related("room", "recipient"),
        id=message_id,
        user=request.user,
        hidden_at__isnull=True,
    )
    if not MessageService.can_view_message(message=message, user=request.user):
        raise Http404("Сообщение не найдено")

    try:
        attachments = create_attachments(
            message=message,
            uploaded_files=request.FILES.getlist("files"),
        )
    except AttachmentValidationError as error:
        return JsonResponse({"error": str(error)}, status=400)

    serialized_attachments = [
        MessageService.serialize_attachment(attachment)
        for attachment in attachments
    ]
    broadcast_attachment_update(message, serialized_attachments)
    return JsonResponse({"attachments": serialized_attachments}, status=201)


@login_required
def create_audio_message(request, room_slug):
    """Атомарно создаёт самостоятельную аудиореплику без фиктивного текста."""
    if request.method != "POST":
        raise Http404("Маршрут аудиосообщения не найден")
    if not is_allowed(
        identifier=f"user:{request.user.id}",
        bucket="audio_message",
        limit=settings.ATTACHMENT_RATE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        return JsonResponse(
            {"error": "Слишком много аудиосообщений. Подождите минуту."},
            status=429,
        )

    room = get_object_or_404(Room, slug=room_slug)
    if room.is_private and not room.memberships.filter(user=request.user).exists():
        raise Http404("Беседа не найдена")

    uploaded_file = request.FILES.get("audio")
    if uploaded_file is None:
        return JsonResponse({"error": "Аудиозапись не найдена."}, status=400)

    try:
        metadata = validate_attachment(uploaded_file)
        if metadata.kind != Attachment.Kind.AUDIO:
            raise AttachmentValidationError("Выбранный файл не является аудиозаписью.")
        uploaded_file, metadata = normalize_audio_attachment(uploaded_file, metadata)
        with transaction.atomic():
            message = MessageService.create_message(
                user_id=request.user.id,
                room=room,
                text="",
            )
            attachment = create_attachment(
                message=message,
                uploaded_file=uploaded_file,
                metadata=metadata,
            )
    except AttachmentValidationError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except AttachmentInspectionUnavailable as error:
        return JsonResponse({"error": str(error)}, status=503)

    message = Message.objects.select_related("room", "user").prefetch_related(
        "attachments", "reactions",
    ).get(pk=message.pk)
    broadcast_message(message)
    return JsonResponse(
        MessageService.serialize_message(message, viewer_id=request.user.id),
        status=201,
    )


@login_required
def delete_message(request, message_id):
    """Позволяет автору удалить свою реплику, а модератору — любую."""
    if request.method != "POST":
        raise Http404("Маршрут удаления не найден")

    message = get_object_or_404(
        Message.objects.select_related("room", "recipient", "user"),
        id=message_id,
        hidden_at__isnull=True,
    )
    if not MessageService.can_view_message(message=message, user=request.user):
        raise Http404("Сообщение не найдено")
    if not MessageService.hide_message(message=message, actor=request.user):
        return JsonResponse(
            {"error": "Нельзя удалить чужое сообщение."},
            status=403,
        )

    broadcast_message_deleted(message)
    return JsonResponse({"message_id": message.id})


@login_required
def report_message(request, message_id):
    """Сохраняет жалобу для модератора, не скрывая реплику автоматически."""
    if request.method != "POST":
        raise Http404("Маршрут жалоб не найден")
    if not is_allowed(
        identifier=f"user:{request.user.id}",
        bucket="message-report",
        limit=settings.MESSAGE_REPORT_RATE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        return JsonResponse({"error": "Слишком много жалоб. Подождите минуту."}, status=429)
    try:
        payload = json.loads(request.body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Некорректная жалоба."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Некорректная жалоба."}, status=400)

    reason = payload.get("reason")
    details = payload.get("details", "")
    if reason not in MessageReport.Reason.values or not isinstance(details, str):
        return JsonResponse({"error": "Укажите причину жалобы."}, status=400)
    details = details.strip()
    if len(details) > 240:
        return JsonResponse({"error": "Комментарий не может быть длиннее 240 символов."}, status=400)

    message = get_object_or_404(
        Message.objects.select_related("room", "recipient", "user"),
        id=message_id,
        hidden_at__isnull=True,
    )
    if not MessageService.can_view_message(message=message, user=request.user):
        raise Http404("Сообщение не найдено")
    if message.user_id == request.user.id:
        return JsonResponse({"error": "На свою реплику жалоба не нужна."}, status=400)

    report, created = create_message_report(
        message=message,
        reporter=request.user,
        reason=reason,
        details=details,
    )
    return JsonResponse({"reported": True, "created": created}, status=201 if created else 200)


@login_required
def notes_page(request):
    """Показывает заметки только их владельцу."""
    return render(
        request,
        "chat/notes.html",
        {
            "notes": request.user.chat_notes.prefetch_related("attachments"),
            "last_room_url": get_last_room_url(request),
        },
    )


@login_required
def create_note(request):
    """Сохраняет текст из поля ввода или доступную пользователю реплику."""
    if request.method != "POST":
        raise Http404("Маршрут заметок не найден")

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {"error": "Некорректные данные заметки."},
            status=400,
        )

    source_message_id = payload.get("source_message_id")
    text = payload.get("text", "")
    if source_message_id is not None:
        if not isinstance(source_message_id, int):
            return JsonResponse(
                {"error": "Некорректная реплика."},
                status=400,
            )
        source_message = get_object_or_404(
            Message.objects.select_related("room", "recipient", "user"),
            id=source_message_id,
            hidden_at__isnull=True,
        )
        if not MessageService.can_view_message(
            message=source_message,
            user=request.user,
        ):
            raise Http404("Реплика не найдена")
        note, created = MessageService.save_note(
            user=request.user,
            text="",
            source_message=source_message,
        )
    else:
        if not isinstance(text, str) or not text.strip():
            return JsonResponse(
                {"error": "Заметка не может быть пустой."},
                status=400,
            )
        if len(text.strip()) > 1000:
            return JsonResponse(
                {"error": "Заметка не может быть длиннее 1000 символов."},
                status=400,
            )
        note, created = MessageService.save_note(
            user=request.user,
            text=text.strip(),
        )

    return JsonResponse(
        {"id": note.id, "created": created, "notes_url": reverse("chat:notes")},
        status=201 if created else 200,
    )


@login_required
def delete_note(request, note_id):
    """Удаляет заметку без влияния на исходное сообщение в чате."""
    if request.method != "POST":
        raise Http404("Маршрут удаления не найден")
    note = get_object_or_404(Note, id=note_id, user=request.user)
    note.delete()
    return redirect("chat:notes")


def broadcast_message_deleted(message):
    """Убирает скрытую реплику у тех же гостей, которые её видели."""
    event = {
        "type": "message_deleted",
        "message_id": message.id,
        "room_slug": message.room.slug,
    }
    channel_layer = get_channel_layer()
    if message.recipient_id:
        group_names = [
            f"chat_user_{message.user_id}",
            f"chat_user_{message.recipient_id}",
        ]
    elif message.room.is_private:
        group_names = [
            f"chat_user_{user_id}"
            for user_id in message.room.memberships.values_list("user_id", flat=True)
        ]
    else:
        group_names = [f"chat_{message.room.slug}"]

    for group_name in group_names:
        async_to_sync(channel_layer.group_send)(group_name, event)


@login_required
def create_private_room(request):
    """Создаёт одну закрытую беседу владельца с приглашёнными участниками."""
    if request.method != "POST":
        return redirect("chat:rooms")

    if Room.objects.filter(
        owner=request.user,
        visibility=Room.Visibility.PRIVATE,
    ).exists():
        messages.error(request, "У вас уже есть собственная закрытая беседа.")
        return redirect("chat:rooms")

    form = PrivateRoomForm(request.POST, user=request.user)
    if not form.is_valid():
        rooms = list(get_visible_rooms(request.user))
        add_unread_counts(rooms, request.user)
        return render(
            request,
            "chat/rooms.html",
            {
                "rooms": [room for room in rooms if not room.is_private],
                "private_rooms": [room for room in rooms if room.is_private],
                "owned_private_room": None,
                "private_room_form": form,
            },
            status=400,
        )

    with transaction.atomic():
        room = Room.objects.create(
            name=form.cleaned_data["name"],
            slug=private_room_slug(form.cleaned_data["name"]),
            description="Закрытая беседа доступна только её участникам.",
            visibility=Room.Visibility.PRIVATE,
            owner=request.user,
        )
        RoomMembership.objects.bulk_create(
            [
                RoomMembership(room=room, user=user)
                for user in (request.user, *form.cleaned_data["members"])
            ]
        )

    messages.success(request, "Закрытая беседа создана. Участники уже добавлены.")
    return redirect("chat:chat", room_slug=room.slug)


@login_required
def update_private_room(request, room_id):
    """Изменяет название и приглашённых участников собственной беседы."""
    if request.method != "POST":
        return redirect("chat:rooms")
    room = get_object_or_404(
        Room,
        id=room_id,
        owner=request.user,
        visibility=Room.Visibility.PRIVATE,
    )
    form = PrivateRoomForm(request.POST, user=request.user, room=room)
    if not form.is_valid():
        rooms = list(get_visible_rooms(request.user))
        add_unread_counts(rooms, request.user)
        return render(
            request,
            "chat/rooms.html",
            {
                "rooms": [item for item in rooms if not item.is_private],
                "private_rooms": [item for item in rooms if item.is_private],
                "owned_private_room": room,
                "private_room_form": form,
            },
            status=400,
        )

    with transaction.atomic():
        previous_member_ids = set(room.members.values_list("id", flat=True))
        room.name = form.cleaned_data["name"]
        room.save(update_fields=("name",))
        new_members = (request.user, *form.cleaned_data["members"])
        room.members.set(new_members)
        new_member_ids = {member.id for member in new_members}

    revoke_private_room_access(
        room_slug=room.slug,
        user_ids=previous_member_ids - new_member_ids,
    )

    messages.success(request, "Настройки закрытой беседы сохранены.")
    return redirect(f"{reverse('chat:rooms')}#closed-conversations")


@login_required
def delete_private_room(request, room_id):
    """Закрывает и удаляет собственную закрытую беседу."""
    if request.method != "POST":
        return redirect("chat:rooms")
    room = get_object_or_404(
        Room,
        id=room_id,
        owner=request.user,
        visibility=Room.Visibility.PRIVATE,
    )
    room_slug = room.slug
    member_ids = list(room.members.values_list("id", flat=True))
    room.delete()
    revoke_private_room_access(room_slug=room_slug, user_ids=member_ids)
    if request.session.get("last_chat_room_slug") == room_slug:
        request.session.pop("last_chat_room_slug", None)
    messages.success(request, "Закрытая беседа удалена.")
    return redirect(f"{reverse('chat:rooms')}#closed-conversations")


@login_required
def leave_private_room(request, room_id):
    """Позволяет приглашённому участнику покинуть закрытую беседу."""
    if request.method != "POST":
        return redirect("chat:rooms")
    room = get_object_or_404(
        Room.objects.filter(memberships__user=request.user),
        id=room_id,
        visibility=Room.Visibility.PRIVATE,
    )
    if room.owner_id == request.user.id:
        messages.error(request, "Создатель может только удалить свою беседу.")
        return redirect(f"{reverse('chat:rooms')}#closed-conversations")

    RoomMembership.objects.filter(room=room, user=request.user).delete()
    revoke_private_room_access(room_slug=room.slug, user_ids=(request.user.id,))
    messages.success(request, "Вы покинули закрытую беседу.")
    return redirect(f"{reverse('chat:rooms')}#closed-conversations")
