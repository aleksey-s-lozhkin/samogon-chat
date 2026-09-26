from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Q
from users.models import PersonalBlock, User, UserReport


def blocked_ids(viewer_id):
    return PersonalBlock.objects.filter(owner_id=viewer_id).values_list("target_id", flat=True)


def pair_blocked(first, second):
    return bool(first and second) and PersonalBlock.objects.filter(
        Q(owner_id=first, target_id=second) | Q(owner_id=second, target_id=first)
    ).exists()


def lock_pair(first, second):
    list(User.objects.select_for_update().filter(pk__in=(first, second)).order_by("pk"))


def refresh_visibility(user_ids):
    for user_id in user_ids:
        async_to_sync(get_channel_layer().group_send)(f"chat_user_{user_id}", {"type": "visibility_changed"})


@transaction.atomic
def set_personal_block(*, owner, target_id, blocked):
    lock_pair(owner.pk, target_id)
    if blocked:
        _, changed = PersonalBlock.objects.get_or_create(owner=owner, target_id=target_id)
    else:
        changed, _ = PersonalBlock.objects.filter(owner=owner, target_id=target_id).delete()
    if changed:
        transaction.on_commit(lambda: refresh_visibility((owner.pk, target_id)))
    return bool(changed)


@transaction.atomic
def report_user(*, reporter, target, reason, details):
    lock_pair(reporter.pk, target.pk)
    return UserReport.objects.get_or_create(
        reporter=reporter, target=target, resolved_at=None,
        defaults={"reason": reason, "details": details},
    )
