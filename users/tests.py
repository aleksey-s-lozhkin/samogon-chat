from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from config.rate_limit import is_allowed


User = get_user_model()


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alex",
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

        self.assertRedirects(response, "/users/profile/")
        self.user.refresh_from_db()
        self.assertEqual(self.user.message_color, "sage")


class AuthenticationHtmxTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="alex",
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
        self.assertEqual(response["HX-Refresh"], "true")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_successful_registration_requests_page_refresh(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
                "password_confirm": "safe-password",
                "invite_code": "bar-secret",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Refresh"], "true")
        self.assertTrue(User.objects.filter(username="new-user").exists())

    @override_settings(REGISTRATION_INVITE_CODE="bar-secret")
    def test_registration_requires_valid_invite_code_when_enabled(self):
        response = self.client.post(
            "/users/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "safe-password",
                "password_confirm": "safe-password",
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
                "password_confirm": "safe-password",
                "invite_code": "bar-secret",
            },
            **self.htmx_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Refresh"], "true")

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
                "password_confirm": "safe-password",
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
