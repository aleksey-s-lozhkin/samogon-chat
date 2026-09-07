# Samogon

[Русская версия](README.md)

Samogon is a responsive Django and Channels chat for developers. It provides
rooms, direct messages, private tables, and a local AI bartender named Semyon.

## Features

- real-time messages, presence, and typing indicators;
- replies, reactions with participant lists, emoji, and safe code blocks;
- direct messages, private rooms, search, and personal notes;
- protected images and documents, up to three files per message;
- moderation, user reports, temporary and permanent bans;
- registration, password recovery, GitHub and Google OAuth;
- installable PWA and opt-in Web Push notifications;
- light, dark, and system themes;
- a versioned chat, profile, and Web Push API with OpenAPI and Swagger.

## Stack

Python 3.14, Django 6.1, Django Channels, PostgreSQL, Redis, Daphne, HTMX,
django-allauth, Ollama, DRF, and drf-spectacular.

## Local development

Python 3.14 and Poetry are required.

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py runserver
```

Run the checks:

```bash
poetry run python manage.py check
poetry run python manage.py spectacular --validate
poetry run python manage.py test
```

Ollama, Redis, OAuth, Turnstile, and Web Push are optional for basic local
development. Configure them only through environment variables. Start with
`.env.example`; never commit real hosts or secrets.

## API

- Swagger UI: `/api/docs/`;
- OpenAPI schema: `/api/schema/`;
- API v1: `/api/v1/`.

Each Django app keeps its REST adapters beside its Web layer and shares the same
models and services. See the [API documentation](docs/api.md) and the
[WebSocket protocol](docs/websocket-protocol.md).

## Structure

```text
chat/                 Chat, messages, rooms, WebSocket, and services
users/                Users, profiles, OAuth, Push, and device API
config/               Settings and top-level URL composition
templates/            Shared server-rendered templates
static/               CSS, JavaScript, and PWA assets
deployment/           Deployment templates and scripts
docs/                 Architecture, specification, API, and operations
```

## Documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/architecture.md)
- [Product specification and roadmap](docs/product-specification.md)
- [Deployment](docs/deployment.md)

After upgrading old notes with attachments, run
`python manage.py backfill_note_attachments` once. It only fills notes whose
attachment copies have not been created yet.
