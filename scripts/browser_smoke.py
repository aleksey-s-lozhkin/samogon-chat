#!/usr/bin/env python
"""Проверяет основной пользовательский маршрут в реальном браузере."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "test-results" / "browser-smoke"
USERNAME = "smoke-owner"
PASSWORD = "Smoke-test-only-2026"

sys.path.insert(0, str(PROJECT_ROOT))


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_server(base_url, process, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Локальный сервер smoke-test завершился раньше времени")
        try:
            with urlopen(f"{base_url}/api/v1/status/", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise TimeoutError("Локальный сервер smoke-test не запустился вовремя")


class SmokeServer:
    def __init__(self, environment):
        self.environment = environment
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            [
                sys.executable,
                "manage.py",
                "runserver",
                "--noreload",
                f"127.0.0.1:{self.port}",
            ],
            cwd=PROJECT_ROOT,
            env=self.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        wait_for_server(self.base_url, self.process)

    def stop(self):
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def prepare_database(environment):
    os.environ.update(environment)
    import django

    django.setup()

    from django.core.management import call_command
    from chat.models import Room
    from users.models import User

    call_command("migrate", verbosity=0, interactive=False)

    owner = User.objects.create_user(
        username=USERNAME,
        password=PASSWORD,
        welcome_pending=False,
    )
    invited_by = User.objects.create_user(
        username="smoke-host",
        password=PASSWORD,
        welcome_pending=False,
    )
    guest = User.objects.create_user(
        username="smoke-guest",
        password=PASSWORD,
        welcome_pending=False,
    )
    Room.objects.update_or_create(
        slug="u-stoyki",
        defaults={
            "name": "У стойки",
            "description": "Основная тестовая беседа.",
        },
    )
    Room.objects.update_or_create(
        slug="vozle-bilyarda",
        defaults={
            "name": "Возле бильярда",
            "description": "Вторая открытая беседа.",
        },
    )
    owned = Room.objects.create(
        name="Релиз без свидетелей",
        slug="smoke-owned-private",
        visibility=Room.Visibility.PRIVATE,
        owner=owner,
    )
    owned.members.set((owner, guest))
    invited = Room.objects.create(
        name="Архитектурный заговор",
        slug="smoke-invited-private",
        visibility=Room.Visibility.PRIVATE,
        owner=invited_by,
    )
    invited.members.set((invited_by, owner))


def login(page, base_url):
    page.goto(f"{base_url}/chat/?auth=login")
    page.locator("#login-username").fill(USERNAME)
    page.locator("#login-password").fill(PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.wait_for_url(f"{base_url}/chat/?auth=login")
    page.locator("#login-modal").wait_for(state="detached")


def open_chat(page, room_slug):
    page.locator(f'a[href="/chat/{room_slug}/"]').first.click()
    page.wait_for_url(f"**/chat/{room_slug}/")
    page.locator(".chat-history-skeleton").wait_for(state="detached")


def assert_composer_inside_viewport(page):
    geometry = page.locator(".chat-composer").evaluate(
        """element => {
            const box = element.getBoundingClientRect();
            return {
                top: box.top,
                bottom: box.bottom,
                height: box.height,
                viewport: window.innerHeight,
            };
        }"""
    )
    if geometry["top"] < -1 or geometry["bottom"] > geometry["viewport"] + 1:
        raise AssertionError(f"Composer вышел за viewport: {geometry}")
    if geometry["height"] < 44:
        raise AssertionError(f"Composer имеет некорректную высоту: {geometry}")


def run_chromium_flow(playwright, server):
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        login(page, server.base_url)
        open_chat(page, "u-stoyki")
        assert_composer_inside_viewport(page)

        message = f"smoke message {time.time_ns()}"
        page.locator("#chat-message-input").fill(message)
        page.locator("#chat-message-submit").click()
        page.locator(".message-text", has_text=message).wait_for()

        open_chat(page, "smoke-owned-private")
        page.get_by_role("heading", name="Релиз без свидетелей").wait_for()
        open_chat(page, "smoke-invited-private")
        page.get_by_role("heading", name="Архитектурный заговор").wait_for()

        server.stop()
        page.locator("#error-message").filter(has_text="Связь потеряна").wait_for(
            timeout=10_000,
        )
        server.start()
        page.locator("#error-message").filter(has_text="Связь восстановлена").wait_for(
            timeout=15_000,
        )
    except Exception:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=ARTIFACTS_DIR / "chromium-desktop-failure.png", full_page=True)
        raise
    finally:
        context.close()
        browser.close()


def run_mobile_layout(playwright, server, engine, viewport):
    browser_type = getattr(playwright, engine)
    browser = browser_type.launch()
    is_android = engine == "chromium"
    context = browser.new_context(
        viewport=viewport,
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; Pixel 5) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
            if is_android else
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
            "Mobile/15E148 Safari/604.1"
        ),
    )
    page = context.new_page()
    try:
        login(page, server.base_url)
        open_chat(page, "u-stoyki")
        assert_composer_inside_viewport(page)
        input_element = page.locator("#chat-message-input")
        input_element.focus()
        header_is_hidden = page.locator(".chat-header").evaluate(
            "element => getComputedStyle(element).display === 'none'"
        )
        if header_is_hidden != is_android:
            raise AssertionError("Некорректный режим мобильной шапки при фокусе")
        if is_android:
            page.set_viewport_size({"width": viewport["width"], "height": 500})
            page.set_viewport_size(viewport)
            page.locator(".chat-header").wait_for(state="visible")
        input_element.blur()
        page.locator(".chat-header").wait_for(state="visible")

        message = page.locator(".message").first
        message.locator(".message-content").click()
        if "is-selected" not in (message.get_attribute("class") or ""):
            raise AssertionError("Действия сообщения не открылись по нажатию")
        page.locator(".chat-composer").click(position={"x": 2, "y": 2})
        if "is-selected" in (message.get_attribute("class") or ""):
            raise AssertionError("Действия сообщения не закрылись вне пузыря")

        page.get_by_role("button", name="Открыть комнаты").click()
        page.locator('[data-room-slug="smoke-owned-private"]').wait_for()
    except Exception:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{engine}-{viewport['width']}x{viewport['height']}-failure.png"
        page.screenshot(path=ARTIFACTS_DIR / filename, full_page=True)
        raise
    finally:
        context.close()
        browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix="samogon-browser-smoke-") as temporary:
        environment = os.environ.copy()
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings_smoke",
                "SAMOGON_SMOKE_DB": str(Path(temporary) / "smoke.sqlite3"),
                "DATABASE_URL": "",
                "REDIS_URL": "",
                "DEBUG": "1",
            }
        )
        prepare_database(environment)
        server = SmokeServer(environment)
        server.start()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                run_chromium_flow(playwright, server)
                run_mobile_layout(
                    playwright,
                    server,
                    "chromium",
                    {"width": 390, "height": 844},
                )
                run_mobile_layout(
                    playwright,
                    server,
                    "webkit",
                    {"width": 320, "height": 693},
                )
        finally:
            server.stop()
    print("Browser smoke-test passed: desktop Chromium, mobile Chromium and WebKit")


if __name__ == "__main__":
    main()
