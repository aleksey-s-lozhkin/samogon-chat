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
            const inputBox = element.querySelector("#chat-message-input")
                .getBoundingClientRect();
            return {
                top: box.top,
                bottom: box.bottom,
                inputTop: inputBox.top,
                inputBottom: inputBox.bottom,
                inputHeight: inputBox.height,
                viewport: window.innerHeight,
            };
        }"""
    )
    if geometry["top"] < -1 or geometry["bottom"] > geometry["viewport"] + 1:
        raise AssertionError(f"Composer вышел за viewport: {geometry}")
    if geometry["inputTop"] < -1 or geometry["inputBottom"] > geometry["viewport"] + 1:
        raise AssertionError(f"Поле ввода вышло за viewport: {geometry}")
    if geometry["inputHeight"] < 44:
        raise AssertionError(f"Поле ввода имеет некорректную высоту: {geometry}")


def run_chromium_flow(playwright, server):
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        login(page, server.base_url)
        open_chat(page, "u-stoyki")
        assert_composer_inside_viewport(page)
        if "Смахните вправо" in (
            page.locator("#chat-message-input").get_attribute("placeholder") or ""
        ):
            raise AssertionError("Поле ввода всё ещё показывает удалённую подсказку свайпа")

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
        if "Смахните вправо" in (input_element.get_attribute("placeholder") or ""):
            raise AssertionError("Поле ввода всё ещё показывает удалённую подсказку свайпа")
        input_element.focus()
        page.locator(".chat-header").wait_for(state="hidden")
        page.locator(".room-header").wait_for(state="hidden")
        page.set_viewport_size({"width": viewport["width"], "height": 500})
        page.evaluate("handleViewportResize()")
        assert_composer_inside_viewport(page)
        page.set_viewport_size(viewport)
        page.locator(".chat-header").wait_for(state="hidden")
        input_element.blur()
        page.locator(".chat-header").wait_for(state="visible")

        # Поле больше не перехватывает ни долгое нажатие, ни свайп.
        input_element.fill("")
        pointer = {
            "pointerId": 41,
            "pointerType": "touch",
            "clientX": 120,
            "clientY": 640,
        }
        input_element.dispatch_event("pointerdown", pointer)
        page.wait_for_timeout(550)
        pointer["clientX"] = 190
        input_element.dispatch_event("pointermove", pointer)
        input_element.dispatch_event("pointerup", pointer)
        if not page.locator("#bartender-recipient").evaluate(
            "element => element.classList.contains('hidden')"
        ):
            raise AssertionError("Жест по полю ошибочно вызвал Семёна")
        input_element.blur()
        page.locator(".chat-header").wait_for(state="visible")

        # Штатный вызов Семёна из меню остаётся рабочим.
        page.get_by_role("button", name="Открыть комнаты").click()
        page.locator('[data-chat-action="bartender"]').click()
        page.locator("#bartender-recipient").wait_for(state="visible")
        page.locator(".chat-header").wait_for(state="hidden")
        assert_composer_inside_viewport(page)
        page.locator("#cancel-bartender-message").click()
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

        if not is_android:
            page.goto(f"{server.base_url}/users/profile/")
            page.locator(".push-diagnostic-details summary").click()
            page.locator("[data-push-diagnostics]").wait_for()
            page.get_by_text(
                "На iPhone и iPad откройте Самогон с экрана «Домой»",
                exact=False,
            ).wait_for()
            if page.locator("[data-push-master]").is_enabled():
                raise AssertionError("Push нельзя включать в обычной вкладке iPhone")
            page.locator("[data-push-report-copy]").click()
            report = page.locator("[data-push-report-output]").input_value()
            if '"standalone": false' not in report or '"appleMobile": true' not in report:
                raise AssertionError("Диагностический отчёт не определил режим iPhone")
            if "endpoint" in report or "p256dh" in report:
                raise AssertionError("Диагностический отчёт содержит секреты подписки")
    except Exception:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{engine}-{viewport['width']}x{viewport['height']}-failure.png"
        page.screenshot(path=ARTIFACTS_DIR / filename, full_page=True)
        raise
    finally:
        context.close()
        browser.close()


def check_auth_entry(playwright, server):
    for engine in (playwright.chromium, playwright.webkit):
        browser = engine.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"{server.base_url}/users/profile/")
            page.locator("#login-username").wait_for()
            assert "/accounts/login/" in page.url
            assert page.locator('#login-form input[name="next"]').input_value() == "/users/profile/"
            page.locator("#login-username").fill(USERNAME)
            page.locator("#login-password").fill(PASSWORD)
            page.locator('#login-form button[type="submit"]').click()
            page.wait_for_url(f"{server.base_url}/users/profile/")
            page.context.clear_cookies()
            page.goto(f"{server.base_url}/accounts/signup/")
            page.locator("#register-username").wait_for()
            assert not page.locator("#login-username").is_visible()
            page.goto(f"{server.base_url}/accounts/password/reset/")
            page.locator("#id_email").wait_for()
        finally:
            browser.close()


def check_registration_retry(playwright, server):
    for engine in (playwright.chromium, playwright.webkit):
        browser = engine.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"{server.base_url}/accounts/signup/")
            page.locator("#register-username").fill("retry-visitor")
            page.locator("#register-email").fill("retry@example.invalid")
            page.locator("#register-password").fill(PASSWORD)
            # Mock only the external challenge; exercise real HTMX requests/events.
            page.evaluate("""() => {
                const widget = document.createElement('div');
                widget.className = 'cf-turnstile';
                document.querySelector('#register-form').append(widget);
                window.challengeResets = 0;
                window.turnstile = {reset: () => window.challengeResets++};
            }""")
            statuses = [200, 400, 403, 429, 500, 0, 200]
            for index, status in enumerate(statuses):
                def respond(route, request, status=status, index=index):
                    if status == 0:
                        route.abort()
                    else:
                        headers = {"HX-Redirect": "/users/profile/"} if index == 6 else {}
                        route.fulfill(status=status, content_type="text/html", headers=headers,
                                      body="" if index == 6 else "Ошибка заполнения формы")
                page.route("**/users/register/", respond)
                page.locator('#register-form button[type="submit"]').click()
                if index == 6:
                    page.wait_for_url(f"{server.base_url}/accounts/login/?next=/users/profile/")
                else:
                    page.wait_for_function("(count) => window.challengeResets === count", arg=index + 1)
                    assert page.locator('#register-form button[type="submit"]').is_enabled()
                    assert page.locator('#register-error').inner_text().strip()
                    assert page.locator('#register-username').input_value() == "retry-visitor"
                    assert page.locator('#register-password').input_value() == PASSWORD
                page.unroute("**/users/register/", respond)
        finally:
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
                "VAPID_PUBLIC_KEY": "smoke-public-key",
                "VAPID_PRIVATE_KEY": "smoke-private-key",
            }
        )
        prepare_database(environment)
        server = SmokeServer(environment)
        server.start()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                check_registration_retry(playwright, server)
                check_auth_entry(playwright, server)
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
