#!/usr/bin/env python3
"""Controlled HTTP/WebSocket load audit for a dedicated Samogon test room."""

import argparse
import asyncio
import json
import math
import os
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from yarl import URL


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percent / 100) - 1)
    return round(ordered[index] * 1000, 1)


def metric(values):
    return {
        "count": len(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "max_ms": round(max(values) * 1000, 1) if values else None,
    }


def websocket_url(base_url, room_slug):
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, f"/ws/chat/{room_slug}/", "", ""))


def load_credentials(path, required):
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError("Credentials file must not be readable by group or others")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < required:
        raise ValueError(f"Credentials file must contain at least {required} entries")
    for index, item in enumerate(data[:required], start=1):
        if not isinstance(item, dict) or not item.get("username"):
            raise ValueError(f"Credential #{index} has no username")
        if not item.get("sessionid") and not item.get("password"):
            raise ValueError(f"Credential #{index} needs sessionid or password")
    return data[:required]


async def authenticated_session(base_url, credential, timeout, request_headers):
    jar = aiohttp.CookieJar(unsafe=base_url.startswith("http://"))
    session = aiohttp.ClientSession(
        cookie_jar=jar,
        timeout=aiohttp.ClientTimeout(total=timeout),
        headers={
            "User-Agent": "SamogonReleaseAudit/1.0",
            **request_headers,
        },
    )
    if credential.get("sessionid"):
        jar.update_cookies(
            {"sessionid": credential["sessionid"]},
            response_url=URL(base_url),
        )
        return session

    async with session.get(f"{base_url}/chat/?auth=login") as response:
        await response.read()
        if response.status != 200:
            await session.close()
            raise RuntimeError(f"Login page returned HTTP {response.status}")
    csrf_cookie = jar.filter_cookies(URL(base_url)).get("csrftoken")
    if csrf_cookie is None:
        await session.close()
        raise RuntimeError("Login page did not set a CSRF cookie")

    async with session.post(
        f"{base_url}/users/login/",
        data={
            "identifier": credential["username"],
            "password": credential["password"],
            "next": "/chat/",
            "csrfmiddlewaretoken": csrf_cookie.value,
        },
        headers={"X-CSRFToken": csrf_cookie.value},
    ) as response:
        payload = await response.json(content_type=None)
        if response.status != 200 or not payload.get("success"):
            await session.close()
            raise RuntimeError(
                f"Login failed for test account #{credential['username']!r}"
            )
    return session


class WebSocketClosedError(RuntimeError):
    pass


async def collect_events(socket, events):
    try:
        while True:
            message = await socket.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    await events.put(json.loads(message.data))
                except json.JSONDecodeError:
                    continue
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }:
                raise WebSocketClosedError(
                    f"WebSocket closed with code {socket.close_code}"
                )
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise WebSocketClosedError(
                    f"WebSocket failed: {socket.exception()!r}"
                )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await events.put(error)


async def wait_for_event(events, event_type, *, text=None, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for WebSocket event {event_type}")
        try:
            payload = await asyncio.wait_for(events.get(), timeout=remaining)
        except asyncio.TimeoutError as error:
            raise TimeoutError(
                f"Timed out waiting for WebSocket event {event_type}"
            ) from error
        if isinstance(payload, Exception):
            raise payload
        if payload.get("type") != event_type:
            continue
        if text is not None and payload.get("message") != text:
            continue
        return payload


async def connect_client(
    base_url,
    room_slug,
    credential,
    timeout,
    request_headers,
):
    session = await authenticated_session(
        base_url,
        credential,
        timeout,
        request_headers,
    )
    started = time.perf_counter()
    events = asyncio.Queue()
    reader = None
    try:
        socket = await session.ws_connect(
            websocket_url(base_url, room_slug),
            heartbeat=25,
            timeout=aiohttp.ClientWSTimeout(ws_receive=timeout),
        )
        reader = asyncio.create_task(collect_events(socket, events))
        await wait_for_event(events, "history", timeout=timeout)
    except Exception:
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        await session.close()
        raise
    return session, socket, events, reader, time.perf_counter() - started


async def measure_http(session, url, semaphore):
    async with semaphore:
        started = time.perf_counter()
        async with session.get(url) as response:
            await response.read()
            if response.status != 200:
                raise RuntimeError(f"HTTP audit request returned {response.status}")
        return time.perf_counter() - started


async def active_client(socket, events, username, hold_seconds, timeout):
    marker = f"release-audit:{username}:{uuid.uuid4().hex[:10]}"
    started = time.perf_counter()
    await socket.send_json({"message": marker})
    await wait_for_event(events, "message", text=marker, timeout=timeout)
    message_latency = time.perf_counter() - started

    deadline = asyncio.get_running_loop().time() + hold_seconds
    while asyncio.get_running_loop().time() < deadline:
        await socket.send_json({"type": "presence_ping"})
        await asyncio.sleep(min(10, max(0, deadline - asyncio.get_running_loop().time())))
    return message_latency


async def measure_bartender(socket, events, samples, timeout):
    latencies = []
    for index in range(samples):
        marker = f"@Семён, ответь словом готово. Проверка {uuid.uuid4().hex[:8]}-{index}"
        started = time.perf_counter()
        await socket.send_json({"message": marker, "bartender_private": True})
        while True:
            payload = await wait_for_event(events, "message", timeout=timeout)
            if payload.get("username") == "Семён":
                latencies.append(time.perf_counter() - started)
                break
    return latencies


async def run(args):
    base_url = args.base_url.rstrip("/")
    request_headers = {}
    if args.host_header:
        request_headers["Host"] = args.host_header
    if args.forwarded_proto:
        request_headers["X-Forwarded-Proto"] = args.forwarded_proto
    client_count = args.active_clients + args.idle_clients
    credentials = load_credentials(args.credentials, client_count)
    if any(not item.get("sessionid") for item in credentials) \
            and args.connection_interval < 6.1:
        raise ValueError(
            "Password login needs --connection-interval 6.1 or greater; "
            "use prepared sessionid credentials for a faster audit"
        )
    sessions = []
    sockets = []
    event_queues = []
    readers = []
    connect_latencies = []
    errors = []

    started_at = time.time()
    try:
        for index, credential in enumerate(credentials):
            try:
                session, socket, events, reader, latency = await connect_client(
                    base_url,
                    args.room,
                    credential,
                    args.timeout,
                    request_headers,
                )
                sessions.append(session)
                sockets.append(socket)
                event_queues.append(events)
                readers.append(reader)
                connect_latencies.append(latency)
            except Exception as error:
                errors.append(f"client-{index + 1}: {type(error).__name__}: {error}")
                break
            if args.connection_interval:
                await asyncio.sleep(args.connection_interval)

        if errors or len(sockets) != client_count:
            detail = "; ".join(errors) if errors else "unknown connection error"
            raise RuntimeError(f"Not all audit clients connected: {detail}")

        try:
            http_semaphore = asyncio.Semaphore(args.http_concurrency)
            http_latencies = await asyncio.gather(*[
                measure_http(
                    session,
                    f"{base_url}/chat/{args.room}/",
                    http_semaphore,
                )
                for session in sessions
            ])
        except Exception as error:
            raise RuntimeError(
                f"Authenticated HTTP stage failed: {type(error).__name__}: {error}"
            ) from error
        try:
            message_latencies = await asyncio.gather(*[
                active_client(
                    sockets[index],
                    event_queues[index],
                    credentials[index]["username"],
                    args.hold_seconds,
                    args.timeout,
                )
                for index in range(args.active_clients)
            ])
        except Exception as error:
            raise RuntimeError(
                f"WebSocket message stage failed: {type(error).__name__}: {error}"
            ) from error
        try:
            bartender_latencies = await measure_bartender(
                sockets[0],
                event_queues[0],
                args.bartender_samples,
                args.bartender_timeout,
            ) if args.bartender_samples else []
        except Exception as error:
            raise RuntimeError(
                f"Bartender stage failed: {type(error).__name__}: {error}"
            ) from error

        return {
            "status": "ok",
            "target": urlsplit(base_url).netloc,
            "room": args.room,
            "active_clients": args.active_clients,
            "idle_clients": args.idle_clients,
            "elapsed_seconds": round(time.time() - started_at, 1),
            "websocket_connect_and_history": metric(connect_latencies),
            "authenticated_http": metric(http_latencies),
            "websocket_message_roundtrip": metric(message_latencies),
            "bartender_response": metric(bartender_latencies),
            "errors": errors,
        }
    finally:
        await asyncio.gather(
            *[socket.close() for socket in sockets],
            return_exceptions=True,
        )
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await asyncio.gather(
            *[session.close() for session in sessions],
            return_exceptions=True,
        )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--room", required=True, help="Dedicated private audit room")
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument(
        "--host-header",
        help="Host header for direct container routing",
    )
    parser.add_argument(
        "--forwarded-proto",
        choices=("http", "https"),
        help="X-Forwarded-Proto for direct container routing",
    )
    parser.add_argument("--active-clients", type=int, default=30)
    parser.add_argument("--idle-clients", type=int, default=30)
    parser.add_argument("--connection-interval", type=float, default=1.1)
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=30,
        help="Concurrent authenticated HTTP requests (use 1 for local SQLite)",
    )
    parser.add_argument("--hold-seconds", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--bartender-samples", type=int, default=3)
    parser.add_argument("--bartender-timeout", type=float, default=60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.active_clients < 1 or args.idle_clients < 0:
        parser.error("client counts must be positive")
    if args.http_concurrency < 1:
        parser.error("--http-concurrency must be positive")
    if args.bartender_samples < 0 or args.bartender_samples > 5:
        parser.error("bartender samples must be between 0 and 5")
    return args


def main():
    args = parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as error:
        report = {
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        descriptor = os.open(
            args.output,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(rendered + "\n")
    if report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
