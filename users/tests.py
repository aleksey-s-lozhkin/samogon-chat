from io import BytesIO
import json
import re
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from config.rate_limit import is_allowed
from allauth.account.models import EmailAddress
from allauth.account.signals import user_signed_up
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin

from users.adapters import SamogonSocialAccountAdapter
from users.models import PushSubscription
from users.services.push import send_admin_push, send_direct_message_push


User = get_user_model()


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alex",
            email="alex@example.com",
            password="test-password",
        )

    def test_profile_requires_authentication(self):
        response = self.client.get("/users/profile/")

        self.assertEqual(response.status_code, 302)

    def test_profile_links_back_to_rooms(self):
        self.client.force_login(self.user)

        response = self.client.get("/users/profile/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/chat/"')
        self.assertContains(response, "Вернуться к комнатам")

    @override_settings(
        VAPID_PUBLIC_KEY="public-key",
        VAPID_PRIVATE_KEY="private-key",
        WEB_PUSH_ENABLED=True,
    )
    def test_profile_offers_voluntary_push_controls(self):
        self.client.force_login(self.user)

        response = self.client.get("/users/profile/")

        self.assertContains(response, "Уведомления на этом устройстве")
        self.assertContains(response, 'data-vapid-key="public-key"')


class PushSubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="push-user", password="password")
        self.client.force_login(self.user)
        self.payload = {
            "endpoint": "https://push.example/subscription/one",
            "keys": {"p256dh": "public-device-key", "auth": "auth-secret"},
            "enabled": True,
            "directMessages": True,
        }

    @override_settings(WEB_PUSH_ENABLED=True)
    def test_subscribe_creates_device_subscription(self):
        response = self.client.post(
            "/users/profile/push/subscribe/",
            data=json.dumps(self.payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, self.user)
        self.assertTrue(subscription.direct_messages_enabled)

    @override_settings(WEB_PUSH_ENABLED=True)
    def test_unsubscribe_only_deletes_current_users_device(self):
        subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint=self.payload["endpoint"],
            p256dh="public-device-key",
            auth="auth-secret",
        )

        response = self.client.post(
            "/users/profile/push/unsubscribe/",
            data=json.dumps({"endpoint": subscription.endpoint}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushSubscription.objects.exists())

    @override_settings(WEB_PUSH_ENABLED=True)
    def test_logout_removes_subscriptions_created_in_current_session(self):
        self.client.post(
            "/users/profile/push/subscribe/",
            data=json.dumps(self.payload),
            content_type="application/json",
        )

        response = self.client.post("/users/logout/")

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertFalse(PushSubscription.objects.exists())

    def test_status_does_not_claim_another_users_device(self):
        another_user = User.objects.create_user(username="another-user")
        PushSubscription.objects.create(
            user=another_user,
            endpoint=self.payload["endpoint"],
            p256dh="public-device-key",
            auth="auth-secret",
        )

        response = self.client.get(
            "/users/profile/push/status/",
            {"endpoint": self.payload["endpoint"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["known"])

    @override_settings(
        WEB_PUSH_ENABLED=True,
        VAPID_PRIVATE_KEY="private-key",
        VAPID_SUBJECT="mailto:test@example.com",
    )
    @patch("users.services.push.webpush")
    def test_direct_push_has_no_message_text_or_sender(self, mocked_webpush):
        PushSubscription.objects.create(
            user=self.user,
            endpoint=self.payload["endpoint"],
            p256dh="public-device-key",
            auth="auth-secret",
        )

        delivered = send_direct_message_push(
            recipient_id=self.user.id,
            room_slug="u-stoyki",
        )

        self.assertEqual(delivered, 1)
        payload = json.loads(mocked_webpush.call_args.kwargs["data"])
        self.assertEqual(payload["body"], "В Самогоне ждёт личная реплика.")
        self.assertNotIn("sender", payload)
        self.assertNotIn("message", payload)
        self.assertEqual(payload["url"], "/chat/u-stoyki/")

    @override_settings(
        WEB_PUSH_ENABLED=True,
        VAPID_PRIVATE_KEY="private-key",
        VAPID_SUBJECT="mailto:test@example.com",
    )
    @patch("users.services.push.webpush")
    def test_gone_subscription_is_removed(self, mocked_webpush):
        from pywebpush import WebPushException

        subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint=self.payload["endpoint"],
            p256dh="public-device-key",
            auth="auth-secret",
        )
        response = type("Response", (), {"status_code": 410})()
        mocked_webpush.side_effect = WebPushException("gone", response=response)

        send_direct_message_push(recipient_id=self.user.id, room_slug="u-stoyki")

        self.assertFalse(PushSubscription.objects.filter(pk=subscription.pk).exists())


    def test_profile_saves_message_color_preference(self):
        self.client.force_login(self.user)

        response = self.client.post(
            "/users/profile/",
            {
                "username": "alex",
                "email": "",
                "message_color": "sage",
            },
        )

        self.assertRedirects(
            response,
            "/users/profile/",
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.message_color, "sage")

    @override_settings(
        STORAGES={
            "default": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
            },
            "staticfiles": {
                "BACKEND": (
                    "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
                ),
            },
        },
    )
    def test_profile_saves_resized_avatar(self):
        """Профиль принимает изображение и сохраняет нормализованный JPEG."""
        self.client.force_login(self.user)
        image_buffer = BytesIO()
        Image.new("RGB", (640, 360), color="#c6753a").save(
            image_buffer,
            format="PNG",
        )
        uploaded_avatar = SimpleUploadedFile(
            "bar.png",
            image_buffer.getvalue(),
            content_type="image/png",
        )

        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                "/users/profile/",
                {
                    "username": "alex",
                    "email": "",
                    "message_color": "amber",
                    "avatar": uploaded_avatar,
                },
            )

        self.assertRedirects(
            response,
            "/users/profile/",
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name.endswith("avatar.jpg"))


class AuthenticationHtmxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="alex",
            email="alex@example.com",
            password="test-password",
        )
        self.htmx_headers = {"HTTP_HX_REQUEST": "true"}

    def test_invalid_login_replaces_error_fragment(self):
        response = self.client.post(
            "/users/login/",
            {"username": "alex", "password": "wrong-password"},
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный логин или пароль")

    def test_successful_login_requests_page_refresh(self):
        response = self.client.post(
            "/users/login/",
            {"username": "alex", "password": "test-password"},
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_user_can_log_in_with_email(self):
        response = self.client.post(
            "/users/login/",
            {
                "identifier": "ALEX@EXAMPLE.COM",
                "password": "test-password",
                "next": "/chat/",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/chat/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_rejects_external_return_url(self):
        response = self.client.post(
            "/users/login/",
            {
                "identifier": "alex",
                "password": "test-password",
                "next": "https://example.com/stolen-session",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response["HX-Redirect"], "/")

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_successful_registration_requests_page_refresh(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
                "invite_code": "bar-secret",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/")
        user = User.objects.get(username="new-user")
        self.assertTrue(user.welcome_pending)

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_registration_requires_valid_invite_code_when_enabled(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
                "invite_code": "wrong-code",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Код приглашения не подошёл")
        self.assertFalse(User.objects.filter(username="new-user").exists())

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_registration_accepts_valid_invite_code(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
                "invite_code": "bar-secret",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/")

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_registration_requires_unique_email(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "ALEX@example.com",
                "password": "safe-password",
                "invite_code": "bar-secret",
            },
            **self.htmx_headers,
        )

        self.assertContains(response, "Аккаунт с таким email уже есть")
        self.assertContains(response, 'id="register-error-email"')
        self.assertContains(response, 'hx-swap-oob="innerHTML"')
        self.assertFalse(User.objects.filter(username="new-user").exists())

    @patch("users.views.request_is_allowed", return_value=False)
    def test_registration_rate_limit_returns_429(self, _mock_rate_limit):
        response = self.client.post(
            "/users/register/",
            {},
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response,
            "Регистрация временно слишком занята",
            status_code=429,
        )

    @patch("users.views.verify_turnstile", return_value=False)
    @override_settings(
        TURNSTILE_ENABLED=True,
        TURNSTILE_SITE_KEY="test-site-key",
        TURNSTILE_SECRET_KEY="test-secret-key",
    )
    def test_registration_rejects_invalid_turnstile(self, _mock_turnstile):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Не удалось подтвердить", status_code=400)
        self.assertFalse(User.objects.filter(username="new-user").exists())

    def test_banned_user_cannot_log_in(self):
        self.user.banned_at = timezone.now()
        self.user.save(update_fields=("banned_at",))

        response = self.client.post(
            "/users/login/",
            {"username": "alex", "password": "test-password"},
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "аккаунт временно недоступен", status_code=403)


class BannedUserMiddlewareTests(TestCase):
    def test_banned_session_is_logged_out_on_next_request(self):
        user = User.objects.create_user(username="banned-user")
        user.banned_at = timezone.now()
        user.save(update_fields=("banned_at",))
        self.client.force_login(user)

        response = self.client.get("/users/profile/")

        self.assertRedirects(response, "/")


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="alex",
            email="alex@example.com",
            password="old-password-42",
        )

    def test_known_email_receives_reset_link(self):
        response = self.client.post(
            "/users/password-reset/",
            {"email": "alex@example.com"},
        )

        self.assertRedirects(
            response,
            "/users/password-reset/sent/",
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Восстановление доступа", mail.outbox[0].subject)
        self.assertIn("/users/password-reset/", mail.outbox[0].body)

    def test_unknown_email_gets_the_same_confirmation(self):
        response = self.client.post(
            "/users/password-reset/",
            {"email": "unknown@example.com"},
        )

        self.assertRedirects(
            response,
            "/users/password-reset/sent/",
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 0)

    @patch("users.views.request_is_allowed", return_value=False)
    def test_password_reset_is_rate_limited(self, _mock_rate_limit):
        response = self.client.post(
            "/users/password-reset/",
            {"email": "alex@example.com"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много запросов", status_code=429)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_link_allows_setting_a_new_password(self):
        self.client.post(
            "/users/password-reset/",
            {"email": "alex@example.com"},
        )
        reset_path = re.search(
            r"http://testserver(?P<path>/users/password-reset/[^\s]+)",
            mail.outbox[0].body,
        ).group("path")

        response = self.client.get(reset_path)
        response = self.client.post(
            response.url,
            {
                "new_password1": "new-comfortable-password-42",
                "new_password2": "new-comfortable-password-42",
            },
        )

        self.assertRedirects(
            response,
            "/users/password-reset/complete/",
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-comfortable-password-42"))


class OAuthAuthenticationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        GITHUB_OAUTH_CLIENT_ID="github-client",
        GITHUB_OAUTH_CLIENT_SECRET="github-secret",
        GOOGLE_OAUTH_CLIENT_ID="google-client",
        GOOGLE_OAUTH_CLIENT_SECRET="google-secret",
    )
    def test_auth_modal_shows_configured_oauth_providers(self):
        response = self.client.get("/chat/")

        self.assertContains(response, "Продолжить с GitHub")
        self.assertContains(response, "Продолжить с Google")
        self.assertContains(response, 'action="/accounts/github/login/"')
        self.assertContains(response, 'action="/accounts/google/login/"')

    def test_auth_modal_hides_unconfigured_oauth_providers(self):
        response = self.client.get("/chat/")

        self.assertNotContains(response, "Продолжить с GitHub")
        self.assertNotContains(response, "Продолжить с Google")

    @override_settings(TURNSTILE_SITE_KEY="production-site-key")
    def test_turnstile_does_not_retry_configuration_errors_forever(self):
        response = self.client.get("/chat/")

        self.assertContains(response, 'data-retry="never"')
        self.assertContains(response, 'data-error-callback="handleTurnstileError"')

    def test_existing_email_is_not_silently_connected(self):
        User.objects.create_user(
            username="local-user",
            email="owner@example.com",
            password="local-password-42",
        )
        request = self.factory.get("/accounts/github/login/callback/")
        request.user = AnonymousUser()
        social_login = SocialLogin(
            account=SocialAccount(provider="github", uid="github-42"),
            email_addresses=[
                EmailAddress(
                    email="OWNER@example.com",
                    verified=True,
                    primary=True,
                )
            ],
        )

        with self.assertRaises(ImmediateHttpResponse) as error:
            SamogonSocialAccountAdapter(request).pre_social_login(
                request,
                social_login,
            )

        self.assertEqual(
            error.exception.response.url,
            "/chat/?auth=login&oauth_error=email_exists",
        )

    def test_oauth_requires_a_verified_email(self):
        request = self.factory.get("/accounts/github/login/callback/")
        request.user = AnonymousUser()
        social_login = SocialLogin(
            account=SocialAccount(provider="github", uid="github-42"),
            email_addresses=[],
        )

        with self.assertRaises(ImmediateHttpResponse) as error:
            SamogonSocialAccountAdapter(request).pre_social_login(
                request,
                social_login,
            )

        self.assertEqual(
            error.exception.response.url,
            "/chat/?auth=login&oauth_error=email_required",
        )

    def test_oauth_signup_marks_user_for_welcome(self):
        user = User.objects.create_user(username="oauth-newcomer")

        user_signed_up.send(
            sender=self.__class__,
            request=self.factory.get("/accounts/github/login/callback/"),
            user=user,
        )

        user.refresh_from_db()
        self.assertTrue(user.welcome_pending)

    @override_settings(
        GITHUB_OAUTH_CLIENT_ID="github-client",
        GITHUB_OAUTH_CLIENT_SECRET="github-secret",
    )
    def test_profile_offers_provider_connection(self):
        user = User.objects.create_user(
            username="profile-user",
            password="local-password-42",
        )
        self.client.force_login(user)

        response = self.client.get("/users/profile/")

        self.assertContains(response, "Способы входа")
        self.assertContains(response, "Подключить")
        self.assertContains(response, "/accounts/github/login/?process=connect")

    def test_user_with_password_can_disconnect_provider(self):
        user = User.objects.create_user(
            username="local-user",
            password="local-password-42",
        )
        account = SocialAccount.objects.create(
            user=user,
            provider="github",
            uid="github-42",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/users/profile/connections/github/disconnect/",
        )

        self.assertRedirects(
            response,
            "/users/profile/",
            fetch_redirect_response=False,
        )
        self.assertFalse(SocialAccount.objects.filter(pk=account.pk).exists())

    def test_oauth_only_user_cannot_disconnect_last_provider(self):
        user = User.objects.create_user(username="oauth-user")
        user.set_unusable_password()
        user.save(update_fields=("password",))
        account = SocialAccount.objects.create(
            user=user,
            provider="github",
            uid="github-42",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/users/profile/connections/github/disconnect/",
            follow=True,
        )

        self.assertContains(response, "Сначала задайте пароль")
        self.assertTrue(SocialAccount.objects.filter(pk=account.pk).exists())


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_rate_limit_blocks_request_after_limit(self):
        arguments = {
            "identifier": "user:42",
            "bucket": "test",
            "limit": 2,
            "window_seconds": 60,
        }

        self.assertTrue(is_allowed(**arguments))
        self.assertTrue(is_allowed(**arguments))
        self.assertFalse(is_allowed(**arguments))


class AdminPushTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.user = User.objects.create_user(username="subscriber")
        self.admin_subscription = PushSubscription.objects.create(
            user=self.admin,
            endpoint="https://push.example/admin",
            p256dh="admin-key",
            auth="admin-auth",
        )
        self.user_subscription = PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example/user",
            p256dh="user-key",
            auth="user-auth",
        )
        self.url = reverse("admin:users_pushsubscription_send")

    def test_send_page_is_superuser_only(self):
        staff = User.objects.create_user(username="staff", is_staff=True)
        self.client.force_login(staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_superuser_sees_send_page_and_changelist_link(self):
        self.client.force_login(self.admin)

        send_response = self.client.get(self.url)
        list_response = self.client.get(
            reverse("admin:users_pushsubscription_changelist")
        )

        self.assertEqual(send_response.status_code, 200)
        self.assertContains(send_response, "Только мои устройства")
        self.assertContains(list_response, "Отправить Web Push")

    @patch("users.admin.send_admin_push")
    def test_self_test_only_selects_admin_devices(self, mocked_send):
        mocked_send.return_value = type(
            "Result", (), {"delivered": 1, "failed": 0, "removed": 0}
        )()
        self.client.force_login(self.admin)

        response = self.client.post(
            self.url,
            {
                "audience": "self",
                "title": "Проверка",
                "body": "Тестовое уведомление",
                "url": "/chat/",
                "confirm": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("admin:users_pushsubscription_changelist"),
        )
        subscriptions = mocked_send.call_args.kwargs["subscriptions"]
        self.assertEqual(list(subscriptions), [self.admin_subscription])

    @patch("users.admin.send_admin_push")
    def test_broadcast_requires_explicit_confirmation(self, mocked_send):
        self.client.force_login(self.admin)

        response = self.client.post(
            self.url,
            {
                "audience": "all",
                "title": "Важно",
                "body": "Объявление",
                "url": "/chat/",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Подтверждаю отправку")
        mocked_send.assert_not_called()

    @override_settings(
        WEB_PUSH_ENABLED=True,
        VAPID_PRIVATE_KEY="private-key",
        VAPID_SUBJECT="mailto:test@example.com",
    )
    @patch("users.services.push.webpush")
    def test_broadcast_sends_visible_admin_content(self, mocked_webpush):
        result = send_admin_push(
            subscriptions=PushSubscription.objects.filter(enabled=True),
            title="Важно",
            body="Бар закроется в 23:00",
            url="/chat/",
        )

        self.assertEqual(result.delivered, 2)
        payload = json.loads(mocked_webpush.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "Важно")
        self.assertEqual(payload["body"], "Бар закроется в 23:00")

    def test_external_broadcast_url_is_rejected(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            self.url,
            {
                "audience": "all",
                "title": "Важно",
                "body": "Объявление",
                "url": "https://example.com/",
                "confirm": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Укажите внутренний путь")
