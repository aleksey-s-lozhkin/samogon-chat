import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie

from config.rate_limit import is_allowed

from .forms import MessageSearchForm, PrivateRoomForm
from .models import (
    Attachment,
    Message,
    Note,
    NoteAttachment,
    Room,
    RoomMembership,
)
from .services.attachments import AttachmentValidationError, create_attachments
from .services.messages import MessageService
from .services.navigation import get_last_room_url


# Порядок повторяет маршрут гостя по бару, а не алфавитный список.
PUBLIC_ROOM_ORDER = (
    "u-stoyki",
    "vozle-bilyarda",
    "kurilka",
    "podval",
    "posle-zakrytiya",
)


def get_visible_rooms(user):
    """Возвращает открытые комнаты и личные столики текущего гостя."""
    rooms = Room.objects.filter(visibility=Room.Visibility.PUBLIC)
    if user.is_authenticated:
        rooms = Room.objects.filter(
            Q(visibility=Room.Visibility.PUBLIC)
            | Q(memberships__user=user),
        ).distinct()
    public_room_order = Case(
        *[
            When(slug=slug, then=Value(position))
            for position, slug in enumerate(PUBLIC_ROOM_ORDER)
        ],
        default=Value(len(PUBLIC_ROOM_ORDER)),
        output_field=IntegerField(),
    )
    return rooms.annotate(room_order=public_room_order).order_by(
        "visibility",
        "room_order",
        "name",
    )


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
    """Создаёт уникальный URL для тайного столика."""
    base_slug = slugify(name) or "taynyy-stolik"
    slug = base_slug
    number = 2
    while Room.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"
        number += 1
    return slug


def rooms_page(request):
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)
    public_rooms = [room for room in rooms if not room.is_private]
    private_rooms = [room for room in rooms if room.is_private]
    owned_private_room = next(
        (room for room in private_rooms if room.owner_id == request.user.id),
        None,
    ) if request.user.is_authenticated else None

    return render(
        request,
        "chat/rooms.html",
        {
            "rooms": public_rooms,
            "private_rooms": private_rooms,
            "owned_private_room": owned_private_room,
            "private_room_form": PrivateRoomForm(user=request.user),
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
        raise Http404("Тайный столик не найден")

    if request.user.is_authenticated:
        MessageService.mark_room_as_read(room=room, user_id=request.user.id)
        request.session["last_chat_room_slug"] = room.slug
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)

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
            "private_rooms": [item for item in rooms if item.is_private],
            "focus_message_id": focus_message_id,
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


def broadcast_attachment_update(message, attachments):
    """Отправляет новые вложения только тем же людям, что видят сообщение."""
    event = {
        "type": "attachment_update",
        "message_id": message.id,
        "attachments": attachments,
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
    """Создаёт один личный столик владельца и приглашает до двух гостей."""
    if request.method != "POST":
        return redirect("chat:rooms")

    if Room.objects.filter(
        owner=request.user,
        visibility=Room.Visibility.PRIVATE,
    ).exists():
        messages.error(request, "У вас уже есть свой тайный столик.")
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
            description="Тайный столик: разговор остаётся между своими.",
            visibility=Room.Visibility.PRIVATE,
            owner=request.user,
        )
        RoomMembership.objects.bulk_create(
            [
                RoomMembership(room=room, user=user)
                for user in (request.user, *form.cleaned_data["members"])
            ]
        )

    messages.success(request, "Тайный столик готов. Гости уже в списке.")
    return redirect("chat:chat", room_slug=room.slug)
