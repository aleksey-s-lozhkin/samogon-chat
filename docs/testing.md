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

## Проверка production-контейнеров

После запуска образа проверьте состояния обоих сервисов:

```bash
docker compose ps
docker inspect --format '{{json .State.Health}}' samogon-web
docker inspect --format '{{json .State.Health}}' samogon-worker
```

Для ручной проверки web снаружи используются два маршрута:

```bash
curl --fail https://app.example.invalid/health/live/
curl --fail https://app.example.invalid/health/ready/
```

`live` подтверждает ответ Daphne, `ready` дополнительно проверяет PostgreSQL и
Redis. Worker отвечает на адресный Celery ping внутри Docker. Plain Docker
Compose показывает состояние `unhealthy`, но сам по этому признаку контейнер не
перезапускает; `restart: unless-stopped` применяется, когда процесс завершился.
Deploy workflow ждёт готовности обоих контейнеров и останавливает выкладку с
последними журналами соответствующего сервиса, если проверка не прошла.
