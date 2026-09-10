# Проверка проекта

## Серверные тесты

```bash
poetry run python manage.py check
poetry run python manage.py spectacular --validate
poetry run python manage.py test
```

## Браузерный smoke-test

Один раз установите браузерные движки:

```bash
poetry run playwright install chromium webkit
```

Запустите проверку:

```bash
poetry run python scripts/browser_smoke.py
```

Сценарий самостоятельно создаёт временную SQLite-базу и тестовых пользователей,
запускает локальный ASGI-сервер на свободном порту и удаляет данные после
завершения. Рабочая локальная и production-базы не используются.

Проверяются:

- вход через обычную форму;
- загрузка истории через WebSocket и отправка реплики;
- переключение между собственной и приглашённой закрытыми беседами;
- потеря соединения при остановке сервера и автоматическое восстановление;
- положение composer внутри viewport на desktop Chromium, мобильном Chromium
  и мобильном WebKit.

При ошибке в `test-results/browser-smoke/` сохраняется снимок только временной
тестовой страницы. Cookies, Playwright trace и production-данные в артефакты не
записываются. В CI снимки доступны семь дней только как артефакт неуспешного
запуска.

