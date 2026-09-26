from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from users.models import ChatStatus, User


def eligible_guests():
    return User.objects.filter(is_active=True, is_superuser=False).filter(
        Q(banned_at__isnull=True) | Q(banned_until__lte=timezone.now())
    ).exclude(username=settings.BARTENDER_USERNAME)


def guest_data(user, labels=None):
    return {
        "id": user.pk, "username": user.username,
        "display_name": user.username,
        "avatar_url": user.avatar.url if user.avatar else None,
        "status_code": user.presence_status,
        "status": (labels if labels is not None else dict(ChatStatus.objects.values_list("code", "label"))).get(user.presence_status, ""),
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
    }
