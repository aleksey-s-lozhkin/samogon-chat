from chat.models import MessageReport
from users.services.push import send_moderator_report_push


def create_message_report(*, message, reporter, reason: str, details: str = ""):
    """Создаёт одну жалобу пользователя и уведомляет только о новой записи."""
    report, created = MessageReport.objects.get_or_create(
        message=message,
        reporter=reporter,
        defaults={"reason": reason, "details": details},
    )
    if created:
        send_moderator_report_push()
    return report, created
