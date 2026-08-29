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
- configurable message colours;
- Semyon, a local Ollama assistant: mention `@Семён` or use the bartender card;
- public or private questions to Semyon.

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

Ollama is optional during local development. Configure it through environment
variables when it is available:

```dotenv
OLLAMA_BASE_URL=http://192.168.0.78:11434
OLLAMA_MODEL=samogon-semen
OLLAMA_TIMEOUT_SECONDS=20
REDIS_URL=redis://127.0.0.1:6379/0
```

For the intended two-VM deployment, see [docs/deployment.md](docs/deployment.md).
Never expose the Ollama port to the public Internet.

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
- выбор оттенка сообщений в профиле;
- Семён на локальной Ollama: упоминание `@Семён` или клик по его карточке;
- вопрос Семёну можно отправить в общий чат или лично.

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

Ollama при локальной разработке необязательна. Если она доступна, задайте
переменные окружения из английского раздела выше. Подробности развёртывания на
двух VM — в [docs/deployment.md](docs/deployment.md). Порт Ollama не должен быть
доступен из интернета.

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
