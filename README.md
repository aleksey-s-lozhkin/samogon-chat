# Samogon / Самогон

## English

Samogon is a cosy real-time Django chat with rooms, private messages, online
presence, and an AI bartender named Semyon. The interface uses server-rendered
templates with HTMX and Alpine.js; real-time delivery is powered by Django
Channels.

### Features

- authenticated WebSocket chat with a 50-message history;
- room presence, online and offline user lists;
- private messages between users;
- one private table per owner with one or two invited guests;
- configurable message colours;
- Semyon, a local Ollama assistant: mention `@Семён` or use the bartender card;
- public or private questions to Semyon.
- moderation through Django Admin: message hiding, timed bans and audit trail;
- optional Cloudflare Turnstile protection for registration.

### Local development

Requirements: Python 3.14 and Poetry.

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py runserver
```

Run the checks and test suite:

```bash
poetry run python manage.py check
poetry run python manage.py test
```

Create local demo accounts for a long user-list check:

```bash
poetry run python manage.py seed_demo_users --count 60
```

The command is blocked outside `DEBUG` by default. It creates offline accounts;
testing real online presence also needs persistent WebSocket connections.

Ollama is optional during local development. Configure it through environment
variables when it is available:

```dotenv
OLLAMA_BASE_URL=http://192.168.0.78:11434
OLLAMA_MODEL=samogon-semen-gemma
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_KEEP_ALIVE=-1
OLLAMA_TEMPERATURE=0.5
OLLAMA_NUM_PREDICT=80
BARTENDER_RESPONSE_MAX_LENGTH=200
REDIS_URL=redis://127.0.0.1:6379/0
```

`OLLAMA_KEEP_ALIVE=-1` keeps the selected model in VRAM between requests.
Use it only when the Ollama server has enough free GPU memory.

For the intended two-VM deployment, see [docs/deployment.md](docs/deployment.md).
Never expose the Ollama port to the public Internet.

The agreed product roadmap is maintained in
[docs/product-specification.md](docs/product-specification.md).

### Project structure

```text
chat/                 Rooms, messages, WebSocket consumer and services
chat/services/        Message, presence and Ollama integrations
users/                Authentication and profile management
config/               Django and Channels configuration
templates/            Landing page templates
static/               Shared front-end assets
docs/                 Deployment documentation
```

## Русский

«Самогон» — уютный чат на Django: комнаты, личные сообщения, присутствие
пользователей онлайн и ИИ-бармен Семён. Интерфейс построен на Django Templates,
HTMX и Alpine.js, а сообщения в реальном времени доставляет Django Channels.

### Возможности

- WebSocket-чат для авторизованных пользователей и история из 50 сообщений;
- пользователи онлайн/офлайн по комнатам;
- личные сообщения;
- один тайный столик на владельца с одним или двумя приглашёнными гостями;
- выбор оттенка сообщений в профиле;
- Семён на локальной Ollama: упоминание `@Семён` или клик по его карточке;
- вопрос Семёну можно отправить в общий чат или лично.
- модерация через Django Admin: скрытие сообщений, временные баны и журнал;
- защита регистрации Cloudflare Turnstile при включённых ключах.

### Локальный запуск

Нужны Python 3.14 и Poetry.

```bash
poetry install
poetry run python manage.py migrate
poetry run python manage.py runserver
```

Проверка проекта и тесты:

```bash
poetry run python manage.py check
poetry run python manage.py test
```

Чтобы проверить длинный список пользователей локально, создайте тестовые
аккаунты:

```bash
poetry run python manage.py seed_demo_users --count 60
```

Команда по умолчанию заблокирована вне `DEBUG`. Она создаёт офлайн-аккаунты;
для проверки реального присутствия понадобятся постоянные WebSocket-подключения.

Ollama при локальной разработке необязательна. Если она доступна, задайте
переменные окружения из английского раздела выше. Подробности развёртывания на
двух VM — в [docs/deployment.md](docs/deployment.md). Порт Ollama не должен быть
доступен из интернета.

Согласованное ТЗ и roadmap следующего этапа — в
[docs/product-specification.md](docs/product-specification.md).

### Архитектура

```text
chat/                 Комнаты, сообщения, WebSocket и сервисы
chat/services/        Сообщения, presence и интеграция с Ollama
users/                Аутентификация и профиль
config/               Настройки Django и Channels
templates/            Шаблоны стартовой страницы
static/               Общие стили и JavaScript
docs/                 Инструкции по развёртыванию
```
