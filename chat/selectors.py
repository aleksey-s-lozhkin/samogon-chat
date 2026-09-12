from django.db.models import Case, IntegerField, Q, Value, When

from chat.models import AtmosphereLine, Room


PUBLIC_ROOM_ORDER = (
    "u-stoyki",
    "vozle-bilyarda",
    "kurilka",
    "podval",
    "posle-zakrytiya",
)


def get_visible_rooms(user):
    """Returns public rooms and private rooms available to the user."""
    rooms = Room.objects.filter(visibility=Room.Visibility.PUBLIC)
    if user.is_authenticated:
        rooms = Room.objects.filter(
            Q(visibility=Room.Visibility.PUBLIC) | Q(memberships__user=user),
        ).distinct()
    public_room_order = Case(
        *[
            When(slug=slug, then=Value(position))
            for position, slug in enumerate(PUBLIC_ROOM_ORDER)
        ],
        default=Value(len(PUBLIC_ROOM_ORDER)),
        output_field=IntegerField(),
    )
    return (
        rooms.select_related("owner")
        .prefetch_related("members")
        .annotate(room_order=public_room_order)
        .order_by(
            "visibility",
            "room_order",
            "name",
        )
    )


def get_published_atmosphere_lines(limit=50):
    """Выдаёт только одобренный активный контент без модельных черновиков."""
    return list(
        AtmosphereLine.objects.filter(
            status=AtmosphereLine.Status.APPROVED,
            is_active=True,
        )
        .order_by("kind", "id")
        .values_list("text", flat=True)[:limit]
    )
