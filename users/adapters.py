from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

User = get_user_model()


class SamogonSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Не объединяет OAuth и локальные аккаунты по одному совпавшему email."""

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing or request.user.is_authenticated:
            return

        verified_emails = {
            address.email.strip().lower()
            for address in sociallogin.email_addresses
            if address.verified and address.email
        }
        if not verified_emails:
            raise ImmediateHttpResponse(
                redirect("/chat/?auth=login&oauth_error=email_required")
            )

        for email in verified_emails:
            if User.objects.filter(email__iexact=email).exists():
                raise ImmediateHttpResponse(
                    redirect("/chat/?auth=login&oauth_error=email_exists")
                )
