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
ADMIN_USERNAME = "smoke-admin"

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
    from chat.models import Attachment, Message, Note, NoteAttachment, Room
    from users.models import User

    call_command("migrate", verbosity=0, interactive=False)

    owner = User.objects.create_user(
        username=USERNAME,
        password=PASSWORD,
        welcome_pending=False,
    )
    User.objects.create_superuser(
        username=ADMIN_USERNAME,
        email="smoke-admin@example.invalid",
        password=PASSWORD,
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
    main_room, _ = Room.objects.update_or_create(
        slug="u-stoyki",
        defaults={
            "name": "У стойки",
            "description": "Основная тестовая беседа.",
        },
    )
    Message.objects.bulk_create([
        Message(user=owner, room=main_room, text=f"history-{index:03d}")
        for index in range(120)
    ])
    Attachment.objects.create(
        message=Message.objects.filter(room=main_room).order_by("-pk").first(),
        file="chat/attachments/smoke-file.txt",
        original_name=f"smoke-{'a' * 72}.txt",
        content_type="text/plain",
        size=1024,
        kind=Attachment.Kind.FILE,
    )
    Note.objects.create(user=owner, text="Проверить резервную копию")
    Note.objects.create(
        user=owner,
        source_message=Message.objects.filter(room=main_room).first(),
        source_author="Гость-бета",
        text="Идея для вечера",
    )
    file_note = Note.objects.create(user=owner, text="Файл к встрече")
    NoteAttachment.objects.create(
        note=file_note,
        file="chat/note-attachments/smoke-plan.txt",
        original_name="план-альфа.txt",
        content_type="text/plain",
        size=1,
        kind="file",
    )
    Note.objects.create(user=guest, text="Чужой секрет")
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
    # A successful login may drop the auth query string during redirect.
    # Wait for the login form to disappear instead of matching that URL.
    page.locator("#login-modal").wait_for(state="detached")


def run_admin_flow(playwright, server):
    """Проверяет кастомную админку на обычной и мобильной ширине."""
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{server.base_url}/admin/")
        page.locator('input[name="username"]').fill(ADMIN_USERNAME)
        page.locator('input[name="password"]').fill(PASSWORD)
        with page.expect_navigation():
            page.locator('button[type="submit"], input[type="submit"]').click()
        if page.url != f"{server.base_url}/admin/":
            raise AssertionError(f"Вход в админку перенаправил на неожиданный адрес: {page.url}")

        page.goto(f"{server.base_url}/admin/diagnostics/")
        page.get_by_role("heading", name="Статистика и диагностика").wait_for()
        page.get_by_text("Активность за 14 дней", exact=True).wait_for()
        page.get_by_role("link", name="Выгрузить сообщения").wait_for()

        colors = page.locator(".samogon-metric-card").first.evaluate(
            "element => ({background: getComputedStyle(element).backgroundColor, "
            "border: getComputedStyle(element).borderColor})"
        )
        if colors["background"] == "rgba(0, 0, 0, 0)":
            raise AssertionError("Карточки статистики потеряли фон темы")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(100)
        geometry = page.locator(".samogon-admin-page").evaluate(
            "element => ({right: element.getBoundingClientRect().right, viewport: innerWidth, "
            "scrollWidth: document.documentElement.scrollWidth})"
        )
        if geometry["right"] > geometry["viewport"] + 1 or geometry["scrollWidth"] > geometry["viewport"] + 1:
            raise AssertionError(f"Админка вышла за мобильный viewport: {geometry}")

        page.get_by_role("link", name="Выгрузить сообщения").click()
        page.get_by_role("heading", name="Выгрузка сообщений").wait_for()
        page.get_by_label("Добавить текст сообщений").check()
        page.get_by_label("Скрыть email, IP, ссылки и упоминания").check()
    except Exception:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=ARTIFACTS_DIR / "admin-failure.png", full_page=True)
        raise
    finally:
        context.close()
        browser.close()


def open_chat(page, room_slug):
    page.locator(f'a[href="/chat/{room_slug}/"]').first.click()
    page.wait_for_url(f"**/chat/{room_slug}/")
    page.locator(".chat-history-skeleton").wait_for(state="detached")


def check_notes_search(page, base_url):
    notes_url = f"{base_url}/chat/notes/"
    page.goto(notes_url)
    search = page.get_by_role("searchbox", name="Найти в заметках")
    search.wait_for()
    bounds = search.bounding_box()
    assert bounds["x"] >= 0
    assert bounds["x"] + bounds["width"] <= page.evaluate("window.innerWidth") + 1
    assert page.locator(".note-card:visible").count() == 3
    assert "Чужой секрет" not in page.locator(".notes-list").inner_text()

    for query, expected in (
        ("РЕЗЕРВНУЮ копию", "Проверить резервную копию"),
        ("гость-бета", "Идея для вечера"),
        ("план-альфа.txt", "Файл к встрече"),
    ):
        search.fill(query)
        assert page.locator(".note-card:visible").count() == 1
        assert expected in page.locator(".note-card:visible").inner_text()
        assert page.url == notes_url

    search.fill("нет совпадений")
    assert page.locator(".note-card:visible").count() == 0
    page.get_by_text("Ничего не нашлось.").wait_for()
    search.fill("")
    assert page.locator(".note-card:visible").count() == 3
    page.goto(f"{base_url}/chat/")


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


def assert_compact_file_attachment(page):
    file_link = page.locator(".message-attachment-file").first
    file_link.wait_for()
    geometry = file_link.evaluate(
        """element => {
            const link = element.getBoundingClientRect();
            const name = element.querySelector('.message-attachment-name').getBoundingClientRect();
            const size = element.querySelector('.message-attachment-size').getBoundingClientRect();
            return {
                height: link.height,
                right: link.right,
                viewport: innerWidth,
                nameCenter: (name.top + name.bottom) / 2,
                sizeCenter: (size.top + size.bottom) / 2,
                nameRight: name.right,
                sizeLeft: size.left,
            };
        }"""
    )
    if (geometry["height"] > 36 or
            abs(geometry["nameCenter"] - geometry["sizeCenter"]) > 2 or
            geometry["nameRight"] > geometry["sizeLeft"] + 1 or
            geometry["right"] > geometry["viewport"] + 1):
        raise AssertionError(f"Вложение не помещается в одну строку: {geometry}")


def assert_composer_audience(page):
    audience = page.locator("#composer-audience")
    assert audience.inner_text() == "Всем в беседе"
    composer_input = page.locator("#chat-message-input")
    counter = page.locator("#message-char-count")
    page.evaluate("""() => {
        const state = document.createElement('div');
        state.className = 'chat-empty-state';
        document.getElementById('chat-log').append(state);
        setComposerPlaceholder(document.getElementById('chat-message-input'));
    }""")
    assert composer_input.get_attribute("placeholder") == "Начните разговор…"
    page.evaluate("""() => {
        document.querySelector('#chat-log .chat-empty-state').remove();
        const message = document.createElement('div');
        message.className = 'message';
        message.dataset.groupAuthor = 'smoke-guest';
        message.innerHTML = '<span class="message-author-name">smoke-guest</span>';
        document.getElementById('chat-log').append(message);
        setComposerPlaceholder(document.getElementById('chat-message-input'));
    }""")
    assert composer_input.get_attribute("placeholder") == (
        "Продолжите разговор с smoke-guest…"
    )
    page.evaluate("""() => {
        document.querySelector('#chat-log .message:last-child').remove();
        setComposerPlaceholder(document.getElementById('chat-message-input'));
    }""")
    assert counter.is_hidden()
    composer_input.fill("x" * 800)
    assert counter.is_visible() and counter.inner_text() == "800 / 1000"
    composer_input.fill("x" * 950)
    assert "is-warning" in (counter.get_attribute("class") or "")
    composer_input.fill("")
    assert counter.is_hidden()
    assert "Основная тестовая беседа." in page.locator(
        '[data-room-slug="u-stoyki"] small'
    ).inner_text()

    page.locator("#note-trigger").click()
    assert audience.inner_text() == "Личная заметка · только вам"
    page.evaluate("setDirectRecipient('smoke-guest')")
    assert audience.inner_text() == "Лично для @smoke-guest"
    assert page.locator("#note-recipient").is_hidden()

    page.locator('[data-chat-action="bartender"]').click()
    assert audience.inner_text() == "Семёну и всем в беседе"
    assert page.locator("#direct-recipient").is_hidden()
    page.locator("#bartender-private").click()
    assert audience.inner_text() == "Лично Семёну"
    assert page.locator("#chat-message-input").get_attribute(
        "placeholder"
    ) == "Это увидит только Семён…"
    page.locator("#cancel-bartender-message").click()
    assert audience.inner_text() == "Всем в беседе"
    page.locator("#chat-message-input").blur()


def assert_composer_send_feedback(page):
    button = page.locator("#chat-message-submit")
    page.evaluate("showComposerSendFeedback()")
    assert button.evaluate(
        "element => getComputedStyle(element, '::after').animationName"
    ) == "composer-pour"
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("""() => {
        document.getElementById('chat-message-submit').classList.remove('is-sending');
        showComposerSendFeedback();
    }""")
    assert "is-sending" not in (button.get_attribute("class") or "")
    page.emulate_media(reduced_motion="no-preference")


def run_chromium_flow(playwright, server):
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        login(page, server.base_url)
        check_notes_search(page, server.base_url)
        open_chat(page, "u-stoyki")
        assert_composer_inside_viewport(page)
        page.wait_for_function("() => document.querySelectorAll('.message').length === 50")
        header_status = page.locator("#room-live-status")
        page.wait_for_function(
            "() => document.querySelector('#room-live-status').textContent.includes('Пока никого, кроме вас')"
        )
        page.evaluate("() => updateTypingUser({username: 'smoke-guest', active: true})")
        if "smoke-guest печатает" not in header_status.inner_text():
            raise AssertionError("Шапка комнаты не показывает набор текста")
        page.evaluate("() => updateTypingUser({username: 'smoke-guest', active: false})")
        if page.locator(".message.is-grouped").count() != 49:
            raise AssertionError("Соседние реплики одного автора не сгруппированы")
        assert_compact_file_attachment(page)
        oldest_visible = page.locator(".message-text", has_text="history-070")
        before_top = page.locator("#chat-log").evaluate("""element => {
            element.scrollTop = 0;
            return [...document.querySelectorAll('.message-text')]
                .find(item => item.textContent === 'history-070')
                .getBoundingClientRect().top;
        }""")
        page.wait_for_function("() => document.querySelectorAll('.message').length === 100")
        if "is-grouped" not in (page.locator(".message", has=oldest_visible).get_attribute("class") or ""):
            raise AssertionError("Группа разорвалась на границе подгруженной истории")
        after_top = oldest_visible.bounding_box()["y"]
        if abs(after_top - before_top) > 5:
            raise AssertionError(
                "Подгрузка истории сдвинула читаемое сообщение: "
                f"before={before_top}, after={after_top}"
            )
        page.locator("#chat-log").evaluate("element => { element.scrollTop = 0; }")
        page.wait_for_function("() => document.querySelectorAll('.message').length === 120")
        page.get_by_text("Это начало переписки", exact=True).wait_for()
        sticky_day = page.locator("#chat-log").evaluate("""log => {
            log.scrollTop = 140;
            const divider = log.querySelector('.day-divider');
            return divider.getBoundingClientRect().top - log.getBoundingClientRect().top;
        }""")
        if not 0 <= sticky_day <= 48:
            raise AssertionError(f"Дата не закрепилась при прокрутке: {sticky_day}")
        # Clicking an early message scrolls it into view and can start pagination.
        # Exercise selection only after the history anchoring checks finish.
        grouped_message = page.locator(".message.is-grouped").first
        if grouped_message.locator(".message-username").is_visible():
            raise AssertionError("Повторное имя автора занимает место в группе")
        grouped_message.locator(".message-content").click()
        if not grouped_message.locator(".message-username").is_visible():
            raise AssertionError("Действия сгруппированного сообщения недоступны")
        grouped_message.locator(".message-content").click()
        yesterday_label = page.evaluate("""() => {
            const savedDay = lastMessageDay;
            const fragment = document.createDocumentFragment();
            const yesterday = new Date();
            yesterday.setDate(yesterday.getDate() - 1);
            lastMessageDay = null;
            appendDayDivider(fragment, yesterday.toISOString());
            lastMessageDay = savedDay;
            return fragment.querySelector('.day-divider')?.textContent;
        }""")
        if yesterday_label != "Вчера":
            raise AssertionError(f"Разделитель предыдущего дня: {yesterday_label}")
        grouping = page.evaluate("""() => {
            const savedDay = lastMessageDay;
            const fragment = document.createDocumentFragment();
            const noon = new Date();
            noon.setHours(12, 0, 0, 0);
            lastMessageDay = null;
            const samples = [
                {private: false},
                {private: false},
                {private: true, recipient: 'smoke-guest'},
                {private: true, recipient: 'smoke-guest'},
                {private: true, recipient: 'smoke-host'},
                {private: true, recipient: 'smoke-host', reply_to: {id: 1, available: false}},
            ];
            samples.forEach((sample, index) => addMessage({
                username: 'smoke-owner',
                message: 'Проверка группы',
                timestamp: new Date(noon.getTime() + index * 60_000).toISOString(),
                ...sample,
            }, {chatLog: fragment, historical: true, suppressScroll: true}));
            const result = [...fragment.querySelectorAll('.message')]
                .map(message => message.classList.contains('is-grouped'));
            lastMessageDay = savedDay;
            return result;
        }""")
        if grouping != [False, True, False, True, False, False]:
            raise AssertionError(f"Группировка смешала режимы или ответы: {grouping}")
        if "Смахните вправо" in (
            page.locator("#chat-message-input").get_attribute("placeholder") or ""
        ):
            raise AssertionError("Поле ввода всё ещё показывает удалённую подсказку свайпа")
        assert_composer_audience(page)
        assert_composer_send_feedback(page)

        message = f"smoke message {time.time_ns()}"
        page.locator("#chat-message-input").fill(message)
        page.locator("#chat-message-submit").click()
        page.locator(".message-text", has_text=message).wait_for()
        if page.locator(".message.own .message-time").last.evaluate(
            "element => getComputedStyle(element).textAlign"
        ) != "right":
            raise AssertionError("Время своего сообщения не со стороны аватара")

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


def run_expired_session_websocket_flow(playwright, server):
    """Проверяет отказ WebSocket после потери cookie на открытой странице."""
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        login(page, server.base_url)
        open_chat(page, "u-stoyki")
        context.clear_cookies()
        page.evaluate("reconnectWebSocketNow()")
        login_link = page.locator("#error-message a")
        login_link.wait_for(timeout=10000)
        assert login_link.inner_text() == "Войти"
        assert login_link.get_attribute("href").endswith(
            "/accounts/login/?next=%2Fchat%2Fu-stoyki%2F"
        )
        assert "Вход устарел" in page.locator("#error-message").inner_text()
        assert page.evaluate("reconnectBlocked && reconnectTimer === null")
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
        check_notes_search(page, server.base_url)
        open_chat(page, "u-stoyki")
        assert_composer_inside_viewport(page)
        assert_compact_file_attachment(page)
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

        page.evaluate("""() => activateReply({
            id: 1,
            username: 'smoke-guest',
            message: 'Длинная цитата '.repeat(25),
        })""")
        quote_geometry = page.locator("#reply-recipient").evaluate("""element => {
            const label = element.querySelector('span').getBoundingClientRect();
            const cancel = element.querySelector('button').getBoundingClientRect();
            return {
                labelHeight: label.height,
                cancelRight: cancel.right,
                viewport: window.innerWidth,
            };
        }""")
        if quote_geometry["labelHeight"] > 24 or quote_geometry["cancelRight"] > quote_geometry["viewport"] + 1:
            raise AssertionError(f"Панель ответа не помещается на экране: {quote_geometry}")
        page.locator("#cancel-reply").click()
        input_element.blur()
        page.locator(".chat-header").wait_for(state="visible")

        # Scrolling to the first message may prepend history. Keep the same
        # message identity when checking selection after the click.
        message_id = page.locator(".message[data-message-id]").first.get_attribute("data-message-id")
        message = page.locator(f'.message[data-message-id="{message_id}"]')
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
                const token = document.createElement('input');
                token.type = 'hidden'; token.name = 'cf-turnstile-response'; token.value = 'test-token';
                document.querySelector('#register-form').append(token);
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


def check_auth_without_htmx(playwright, server):
    for engine in (playwright.chromium, playwright.webkit):
        browser = engine.launch()
        try:
            for javascript in (False, True):
                context = browser.new_context(java_script_enabled=javascript, viewport={"width": 390, "height": 844})
                page = context.new_page()
                page.route("**/vendor/htmx/**", lambda route: route.abort())
                requests = []
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.goto(f"{server.base_url}/accounts/login/?next=/users/profile/")
                page.locator("#login-username").fill(USERNAME)
                page.locator("#login-password").fill(PASSWORD)
                page.locator('#login-form button[type="submit"]').click()
                page.wait_for_url(f"{server.base_url}/users/profile/")
                assert any(method == "POST" and url.endswith("/users/login/") for method, url in requests)
                assert all(PASSWORD not in url and "password=" not in url for _, url in requests)
                context.clear_cookies()
                page.goto(f"{server.base_url}/accounts/signup/")
                page.locator("#register-username").fill(f"native-{engine.name}-{javascript}")
                page.locator("#register-email").fill(f"native-{engine.name}-{javascript}@example.invalid")
                page.locator("#register-password").fill(PASSWORD)
                page.locator('#register-form button[type="submit"]').click()
                page.wait_for_url(f"{server.base_url}/chat/")
                assert any(method == "POST" and url.endswith("/users/register/") for method, url in requests)
                assert all("password=" not in url and "email=" not in url for _, url in requests)
                context.close()
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
                "REGISTRATION_OPEN": "1",
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
                check_auth_without_htmx(playwright, server)
                check_registration_retry(playwright, server)
                check_auth_entry(playwright, server)
                run_admin_flow(playwright, server)
                run_chromium_flow(playwright, server)
                run_expired_session_websocket_flow(playwright, server)
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
