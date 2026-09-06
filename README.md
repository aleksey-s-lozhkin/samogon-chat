# Самогон

[English version](README.en.md)

«Самогон» — адаптивный чат для разработчиков на Django и Channels с комнатами,
личными сообщениями, закрытыми столиками и локальным ИИ-барменом Семёном.

## Возможности

- сообщения, присутствие и индикатор набора в реальном времени;
- ответы, реакции с просмотром участников, emoji и безопасные блоки кода;
- личные сообщения, приватные комнаты, поиск и личные заметки;
- защищённые изображения и документы до трёх файлов на сообщение;
- модерация, жалобы, временные и постоянные блокировки;
- регистрация, восстановление пароля, GitHub и Google OAuth;
- устанавливаемая PWA и добровольные Web Push-уведомления;
- светлая, тёмная и системная темы;
- версионированный диагностический API и OpenAPI/Swagger.

## Стек

Python 3.14, Django 6.1, Django Channels, PostgreSQL, Redis, Daphne, HTMX,
django-allauth, Ollama, DRF и drf-spectacular.

## Локальный запуск

Нужны Python 3.14 и Poetry.

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py runserver
```

Проверки:

```bash
poetry run python manage.py check
poetry run python manage.py spectacular --validate
poetry run python manage.py test
```

Ollama, Redis, OAuth, Turnstile и Web Push необязательны для базовой локальной
разработки. Их параметры задаются только переменными окружения. Начните с
`.env.example`; реальные адреса и секреты в репозиторий не добавляются.

## API

- Swagger UI: `/api/docs/`;
- OpenAPI schema: `/api/schema/`;
- API v1: `/api/v1/`.

REST API каждого приложения находится рядом с его Web-слоем и использует те же
модели и сервисы. Подробнее: [документация API](docs/api.md) и
[протокол WebSocket](docs/websocket-protocol.md).

## Структура

```text
chat/                 Чат, сообщения, комнаты, WebSocket и сервисы
users/                Пользователи, профиль, OAuth, Push и API устройств
config/               Настройки и сборка маршрутов
templates/            Общие серверные шаблоны
static/               CSS, JavaScript и PWA-ресурсы
deployment/           Шаблоны и сценарии развёртывания
docs/                 Архитектура, ТЗ, API и эксплуатация
```

## Документация

- [Оглавление](docs/README.md)
- [Архитектура](docs/architecture.md)
- [Техническое задание и roadmap](docs/product-specification.md)
- [Развёртывание](docs/deployment.md)

После обновления старых заметок с вложениями один раз выполните
`python manage.py backfill_note_attachments`. Команда дополняет только записи,
для которых копии вложений ещё не созданы.
