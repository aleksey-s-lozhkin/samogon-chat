#!/usr/bin/env python3
"""Read-only production boundary checks with optional authenticated WebSocket."""

import argparse
import asyncio
import json
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from yarl import URL


PUBLIC_CHECKS = (
    ("liveness", "/health/live/", "application/json"),
    ("readiness", "/health/ready/", "application/json"),
    ("home", "/", "text/html"),
    ("manifest", "/manifest.webmanifest", "application/manifest+json"),
    ("service_worker", "/service-worker.js", "application/javascript"),
    ("rules", "/rules/", "text/html"),
    ("openapi", "/api/schema/", None),
)


def ws_url(base_url, room_slug):
    parts = urlsplit(base_url)
    return urlunsplit((
        "wss" if parts.scheme == "https" else "ws",
        parts.netloc,
        f"/ws/chat/{room_slug}/",
        "",
        "",
    ))


async def check_public(session, base_url, name, path, content_type):
    started = time.perf_counter()
    async with session.get(f"{base_url}{path}") as response:
        body = await response.read()
        if response.status != 200:
            raise RuntimeError(f"{name} returned HTTP {response.status}")
        if content_type and content_type not in response.headers.get("Content-Type", ""):
            raise RuntimeError(f"{name} returned an unexpected content type")
        if not body:
            raise RuntimeError(f"{name} returned an empty response")
        if name == "readiness":
            payload = json.loads(body)
            if payload.get("status") != "ok":
                raise RuntimeError("readiness reports unavailable dependencies")
        if name == "manifest":
            payload = json.loads(body)
            if not payload.get("icons") or payload.get("display") != "standalone":
                raise RuntimeError("PWA manifest is incomplete")
    return name, round((time.perf_counter() - started) * 1000, 1)


def read_first_session(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Credentials file must have mode 600")
    records = json.loads(path.read_text(encoding="utf-8"))
    if not records or not records[0].get("sessionid"):
        raise ValueError("Credentials file has no audit session")
    return records[0]


async def check_authenticated(session, base_url, room_slug, timeout):
    started = time.perf_counter()
    async with session.get(f"{base_url}/chat/{room_slug}/") as response:
        body = await response.text()
        if response.status != 200 or "chat-message-input" not in body:
            raise RuntimeError("Authenticated chat page is unavailable")
    http_ms = round((time.perf_counter() - started) * 1000, 1)

    started = time.perf_counter()
    async with session.ws_connect(ws_url(base_url, room_slug), heartbeat=25) as socket:
        while True:
            message = await socket.receive(timeout=timeout)
            if message.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError("WebSocket closed before history")
            payload = json.loads(message.data)
            if payload.get("type") == "history":
                break
    websocket_ms = round((time.perf_counter() - started) * 1000, 1)
    return {"chat_http_ms": http_ms, "websocket_history_ms": websocket_ms}


MOBILE_OPERATIONS = {
    "/api/v1/chat/rooms/": {"get", "post"},
    "/api/v1/chat/rooms/{room_slug}/": {"get", "patch", "delete"},
    "/api/v1/chat/rooms/{room_slug}/leave/": {"post"},
    "/api/v1/chat/guests/": {"get"},
    "/api/v1/users/statuses/": {"get"},
    "/api/v1/users/{user_id}/reports/": {"post"},
    "/api/v1/users/me/blocks/": {"get"},
    "/api/v1/users/me/blocks/{user_id}/": {"put", "delete"},
}


async def check_mobile_api(session, base_url):
    """Read-only checks; never print account/room data or session credentials."""
    async def get(path):
        async with session.get(base_url + path, allow_redirects=False,
                               headers={"Accept": "application/json"}) as response:
            if response.status != 200:
                raise RuntimeError(f"Mobile API returned HTTP {response.status}")
            return await response.json()

    schema = await get("/api/schema/?format=json")
    for path, methods in MOBILE_OPERATIONS.items():
        if not methods.issubset(schema.get("paths", {}).get(path, {})):
            raise RuntimeError("Published mobile schema is incomplete")
    guests = await get("/api/v1/chat/guests/")
    statuses = await get("/api/v1/users/statuses/")
    blocks = await get("/api/v1/users/me/blocks/")
    rooms = await get("/api/v1/chat/rooms/")
    if not all(payload.get("api_version") == "v1" for payload in (guests, statuses, blocks, rooms)):
        raise RuntimeError("Unexpected mobile API version")
    if not isinstance(guests.get("online"), list) or not isinstance(statuses.get("statuses"), list):
        raise RuntimeError("Incomplete presence or status response")
    checked = 0
    private_checked = 0
    for room in rooms["rooms"]:
        detail = await get("/api/v1/chat/rooms/" + room["slug"] + "/")
        if not all(key in detail for key in ("owner", "members", "can_manage", "can_leave", "can_delete")):
            raise RuntimeError("Room management metadata is missing")
        checked += 1
        private_checked += detail["owner"] is not None
    return {"schema": "ok", "guests": "ok", "statuses": "ok", "blocks": "ok",
            "room_details_checked": checked, "private_rooms_checked": private_checked,
            "mutations": "not_run"}


async def run(args):
    if args.mobile_api and not args.credentials:
        raise ValueError("--mobile-api requires --credentials")
    base_url = args.base_url.rstrip("/")
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "SamogonProductionSmoke/1.0"},
    ) as public_session:
        results = await asyncio.gather(*[
            check_public(public_session, base_url, *check)
            for check in PUBLIC_CHECKS
        ])

    report = {
        "status": "ok",
        "target": urlsplit(base_url).netloc,
        "public": dict(results),
    }
    if args.credentials:
        credential = read_first_session(args.credentials)
        jar = aiohttp.CookieJar(unsafe=base_url.startswith("http://"))
        jar.update_cookies(
            {"sessionid": credential["sessionid"]},
            response_url=URL(base_url),
        )
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
            report["authenticated"] = await check_authenticated(
                session,
                base_url,
                args.room,
                args.timeout,
            )
            if args.mobile_api:
                report["mobile_api"] = await check_mobile_api(session, base_url)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--room", default="release-audit")
    parser.add_argument("--mobile-api", action="store_true", help="Check published schema and authenticated mobile GET endpoints")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as error:
        report = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
