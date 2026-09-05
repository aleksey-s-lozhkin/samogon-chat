from allauth.account.signals import user_signed_up
from django.dispatch import receiver


@receiver(user_signed_up)
def mark_social_signup_for_welcome(sender, request, user, **kwargs):
    """Помечает созданный через OAuth аккаунт для первого приветствия в чате."""
    if not user.welcome_pending:
        user.welcome_pending = True
        user.save(update_fields=("welcome_pending",))
