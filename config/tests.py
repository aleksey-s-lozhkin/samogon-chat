from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class HealthEndpointTests(SimpleTestCase):
    def test_liveness_is_public_and_not_cached(self):
        response = self.client.get(reverse("health_live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_liveness_rejects_non_get_requests(self):
        response = self.client.post(reverse("health_live"))

        self.assertEqual(response.status_code, 405)

    @patch("config.views.readiness_status")
    def test_readiness_reports_safe_component_state(self, readiness_status):
        readiness_status.return_value = (
            True,
            {"database": "ok", "redis": "ok"},
        )

        response = self.client.get(reverse("health_ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "components": {"database": "ok", "redis": "ok"},
            },
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    @patch("config.views.readiness_status")
    def test_readiness_returns_503_without_internal_error_details(
        self,
        readiness_status,
    ):
        readiness_status.return_value = (
            False,
            {"database": "unavailable", "redis": "ok"},
        )

        response = self.client.get(reverse("health_ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "unavailable",
                "components": {"database": "unavailable", "redis": "ok"},
            },
        )
        self.assertNotContains(response, "password", status_code=503)

    def test_health_endpoints_are_in_openapi(self):
        response = self.client.get(reverse("api_schema"))

        self.assertEqual(response.status_code, 200)
        schema = response.content.decode()
        self.assertIn("/health/live/", schema)
        self.assertIn("/health/ready/", schema)


class DependencyHealthTests(SimpleTestCase):
    @patch("config.health.connection")
    def test_database_failure_is_reduced_to_boolean(self, connection):
        connection.cursor.side_effect = RuntimeError("secret database address")

        from config.health import check_database

        self.assertIs(check_database(), False)
        connection.close.assert_called_once_with()

    @patch("config.health.cache")
    def test_cache_failure_is_reduced_to_boolean(self, cache):
        cache.get.side_effect = RuntimeError("secret redis address")

        from config.health import check_cache

        self.assertIs(check_cache(), False)

    @patch("config.health.urlopen")
    def test_web_probe_uses_readiness_endpoint(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200

        from config.health import web_is_ready

        self.assertIs(web_is_ready(3), True)
        urlopen.assert_called_once_with(
            "http://127.0.0.1:8000/health/ready/",
            timeout=3,
        )

    @patch("config.health.socket.gethostname", return_value="worker-test")
    @patch("config.celery.app.control.inspect")
    def test_worker_probe_pings_only_current_worker(self, inspect, gethostname):
        inspect.return_value.ping.return_value = {
            "celery@worker-test": {"ok": "pong"},
        }

        from config.health import worker_is_ready

        self.assertIs(worker_is_ready(4), True)
        inspect.assert_called_once_with(
            destination=["celery@worker-test"],
            timeout=4,
        )

    @patch("config.health.socket.gethostname", return_value="worker-test")
    @patch("config.celery.app.control.inspect")
    def test_worker_probe_fails_when_current_worker_does_not_reply(
        self,
        inspect,
        gethostname,
    ):
        inspect.return_value.ping.return_value = None

        from config.health import worker_is_ready

        self.assertIs(worker_is_ready(4), False)
