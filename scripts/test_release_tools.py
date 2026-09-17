import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp

from scripts.performance_audit import (
    collect_events,
    connect_client,
    load_credentials,
    percentile,
    wait_for_event,
    websocket_url,
)
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


class PerformanceAuditAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_starts_reader_before_waiting_for_history(self):
        session = AsyncMock()
        socket = AsyncMock()
        session.ws_connect.return_value = socket
        events = asyncio.Queue()

        with (
            patch(
                "scripts.performance_audit.authenticated_session",
                AsyncMock(return_value=session),
            ),
            patch(
                "scripts.performance_audit.collect_events",
                AsyncMock(side_effect=lambda _socket, queue: queue.put_nowait({
                    "type": "history",
                })),
            ),
        ):
            _session, _socket, events, reader, _latency = await connect_client(
                "http://samogon-web:8000",
                "release-audit",
                {"username": "audit", "sessionid": "secret"},
                30,
                {},
            )

        self.assertEqual(session.ws_connect.await_args.kwargs["heartbeat"], 25)
        self.assertIsInstance(events, asyncio.Queue)
        await reader

    async def test_collector_routes_text_events_to_queue(self):
        socket = AsyncMock()
        socket.receive.side_effect = [
            aiohttp.WSMessage(
                aiohttp.WSMsgType.TEXT,
                '{"type":"history"}',
                None,
            ),
            aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, None, None),
        ]
        socket.close_code = 1000
        events = asyncio.Queue()

        await collect_events(socket, events)

        self.assertEqual(await events.get(), {"type": "history"})
        self.assertRegex(str(await events.get()), "closed with code 1000")

    async def test_websocket_timeout_names_the_waited_event(self):
        events = asyncio.Queue()
        with self.assertRaisesRegex(TimeoutError, "WebSocket event history"):
            await wait_for_event(events, "history", timeout=0.01)


if __name__ == "__main__":
    unittest.main()
