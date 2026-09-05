# Декомпозиция кода

## Уже сделано

- Общий header страниц чата вынесен в
  `chat/templates/chat/partials/site_header.html`.
- Footer страницы комнат вынесен в
  `chat/templates/chat/partials/rooms_footer.html`.
- Интеграция Ollama находится за `BartenderService`, а не внутри view.
- Доступ к сообщениям и presence уже отделён в `chat/services/`.
- Web Push изолирован в `users/services/push.py`: браузерные подписки принадлежат
  пользователю и устройству, а `ChatConsumer` передаёт сервису только ID
  получателя и комнату. Текст сообщения и имя автора в push payload не входят.

## Следующие безопасные шаги

### 1. Базовые HTML-шаблоны

Создать `templates/base.html` для `<html>`, `<head>`, мета-тегов и подключения
общих ресурсов. Затем `base_chat.html`, `base_profile.html` и `base_home.html`
будут определять только нужный CSS и содержимое блоков. Это уменьшит
дублирование, но не следует смешивать стили разных страниц в один файл.

### 2. Части страницы чата

`chat.html` всё ещё большой. Его стоит разделить на partials:

- `chat/partials/auth_modal.html`;
- `chat/partials/sidebar.html`;
- `chat/partials/room_header.html`;
- `chat/partials/composer.html`.

Шаблоны останутся серверными, поэтому логика HTMX и Alpine.js не изменится.

### 3. WebSocket consumer

`ChatConsumer` сейчас отвечает сразу за протокол, права, сохранение и
рассылку. Следующий шаг — вынести в отдельные функции/сервисы:

- `ChatEventFactory` — единый JSON-формат событий;
- `RecipientResolver` — проверка получателя и режима вопроса Семёну;
- `ChatBroadcaster` — отправка в group/channel layer.

Это позволит тестировать правила без `WebsocketCommunicator`.

### 4. Селекторы и сервисы данных

`MessageService` можно разделить на `MessageSelector` (чтение истории) и
`MessageService` (создание сообщения). Это особенно полезно перед добавлением
пагинации, непрочитанных сообщений и поиска.

### 5. Конфигурация развёртывания

Настройки сейчас используют безопасные для разработки значения. Перед
production стоит выделить `config/settings/base.py`, `development.py` и
`production.py`, чтобы `DEBUG`, база данных, Redis, секреты и `ALLOWED_HOSTS`
не жили в одном файле.

## Что пока не стоит усложнять

- Celery: не нужен, пока Семён отвечает по запросу и нет фоновых задач.
- DRF: оправдан только с мобильным клиентом или публичным API.
- отдельный сервис личных диалогов: нужен после появления списка диалогов и
  более сложных правил непрочитанного; текущие Web Push этого не требуют.
