import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.performance_audit import load_credentials, percentile, websocket_url
from scripts.production_smoke import ws_url


class PerformanceAuditHelpersTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([0.1, 0.2, 0.3, 0.4], 50), 200.0)
        self.assertEqual(percentile([0.1, 0.2, 0.3, 0.4], 95), 400.0)

    def test_websocket_urls_preserve_host_and_choose_secure_scheme(self):
        self.assertEqual(
            websocket_url("https://sam.example", "release-audit"),
            "wss://sam.example/ws/chat/release-audit/",
        )
        self.assertEqual(
            ws_url("http://127.0.0.1:8000", "general"),
            "ws://127.0.0.1:8000/ws/chat/general/",
        )

    def test_credentials_file_must_be_private_and_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.json"
            path.write_text(
                json.dumps([{"username": "audit", "sessionid": "secret"}]),
                encoding="utf-8",
            )
            os.chmod(path, 0o600)
            self.assertEqual(load_credentials(path, 1)[0]["username"], "audit")
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ValueError, "must not be readable"):
                load_credentials(path, 1)


if __name__ == "__main__":
    unittest.main()
