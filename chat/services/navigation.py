from django.urls import reverse

from chat.models import Room


def get_last_room_url(request) -> str:
    """Возвращает гостя к последней доступной беседе, а не к списку."""
    room_slug = request.session.get("last_chat_room_slug")
    if not room_slug or not request.user.is_authenticated:
        return reverse("chat:rooms")

    room = Room.objects.filter(slug=room_slug).first()
    if room and (
        not room.is_private
        or room.memberships.filter(user=request.user).exists()
    ):
        return reverse("chat:chat", args=[room.slug])
    return reverse("chat:rooms")
