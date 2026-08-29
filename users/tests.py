from django.contrib.auth import get_user_model
from django.test import TestCase


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

    def test_successful_registration_requests_page_refresh(self):
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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Refresh"], "true")
        self.assertTrue(User.objects.filter(username="new-user").exists())
