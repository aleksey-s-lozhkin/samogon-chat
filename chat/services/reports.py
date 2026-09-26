from django.db import transaction
from chat.models import Message, MessageReport
from users.services.push import send_moderator_report_push


@transaction.atomic
def create_message_report(*, message, reporter, reason: str, details: str = ""):
    """Создаёт одну жалобу пользователя и уведомляет только о новой записи."""
    Message.objects.select_for_update().get(pk=message.pk)
    report, created = MessageReport.objects.get_or_create(
        message=message,
        reporter=reporter,
        defaults={"reason": reason, "details": details},
    )
    if created:
        send_moderator_report_push()
    return report, created
