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


async def run(args):
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
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--room", default="release-audit")
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
