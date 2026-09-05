from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from chat.models import Message


User = get_user_model()
WELCOME_TEXT = (
    "Добро пожаловать в Самогон. Выбирай комнаты в меню, нажми имя гостя для "
    "личной реплики, а если нужна помощь — позови меня: @Семён."
)


def ensure_welcome_message(*, user_id: int, room) -> Message | None:
    """Один раз создаёт личное приветствие в первой открытой гостем комнате."""
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if not user.welcome_pending:
            return None

        bartender, created = User.objects.get_or_create(
            username=settings.BARTENDER_USERNAME,
            defaults={"is_active": False},
        )
        if created:
            bartender.set_unusable_password()
            bartender.save(update_fields=("password",))

        message = Message.objects.create(
            user=bartender,
            room=room,
            recipient=user,
            text=WELCOME_TEXT,
        )
        user.welcome_pending = False
        user.save(update_fields=("welcome_pending",))
        return message
