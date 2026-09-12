from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from chat.models import Message, Room


@dataclass(frozen=True)
class BartenderContextEntry:
    author: str
    text: str


@dataclass(frozen=True)
class BartenderConversation:
    history: tuple[BartenderContextEntry, ...]
    current_author: str


def build_bartender_conversation(
    *,
    room: Room,
    question: Message,
    user_id: int,
    private: bool = False,
):
    """Возвращает только безопасную публичную историю с псевдонимами."""
    if private or room.is_private:
        return BartenderConversation(history=(), current_author="guest_1")

    messages = list(
        Message.objects.filter(
            room=room,
            recipient__isnull=True,
            hidden_at__isnull=True,
        )
        .filter(
            Q(created_at__lt=question.created_at)
            | Q(created_at=question.created_at, pk__lt=question.pk)
        )
        .select_related("user")
        .order_by("-created_at", "-pk")[: settings.BARTENDER_CONTEXT_MESSAGE_LIMIT]
    )
    messages.reverse()

    aliases = {}

    def author_for(message_user_id, username):
        if username == settings.BARTENDER_USERNAME:
            return "semen"
        if message_user_id not in aliases:
            aliases[message_user_id] = f"guest_{len(aliases) + 1}"
        return aliases[message_user_id]

    history = tuple(
        BartenderContextEntry(
            author=author_for(message.user_id, message.user.username),
            text=message.text[: settings.BARTENDER_CONTEXT_MESSAGE_MAX_CHARS],
        )
        for message in messages
    )
    current_author = aliases.get(user_id)
    if current_author is None:
        current_author = f"guest_{len(aliases) + 1}"
    return BartenderConversation(history=history, current_author=current_author)
