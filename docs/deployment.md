# Production-деплой Самогона

## Рекомендуемая схема сервера

Samogon запускается на хосте приложения рядом с существующими PostgreSQL,
Redis и Nginx. Конкретные имена серверов и адреса хранятся вне репозитория.

```text
app.example.invalid → nginx → samogon-web:8000
                                  ├── postgres:5432
                                  ├── redis:6379
                                  └── Ollama ollama.internal:11434
```

Все контейнеры находятся в существующей внешней Docker-сети `infra`.

## 1. Перед запуском

DNS `app.example.invalid` должен указывать на публичный адрес балансировщика,
а порты 80 и 443 — вести на хост приложения. Для TLS используется
отдельный сертификат Let's Encrypt. Создайте каталоги для проверки и сертификатов:

```bash
sudo mkdir -p /srv/data/certbot /srv/data/letsencrypt
```

В сервис `nginx` файла `/srv/compose/nginx/compose.yaml` добавьте mount:

```yaml
      - /srv/data/certbot:/var/www/certbot:ro
      - /srv/data/letsencrypt:/etc/letsencrypt:ro
```

Сначала включите временный HTTP-конфиг с каталогом
`/.well-known/acme-challenge/`, убедитесь, что тестовый файл доступен извне,
и выпустите сертификат:

```bash
sudo docker run --rm \
  -v /srv/data/certbot:/var/www/certbot \
  -v /srv/data/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d app.example.invalid --email admin@example.invalid \
  --agree-tos --no-eff-email
```

## 2. Создание БД и пользователя

Сгенерируйте пароль без пробелов и символов URL:

```bash
openssl rand -base64 32
```

Зайдите в существующий PostgreSQL:

```bash
docker exec -it postgres psql -U postgres -d postgres
```

В консоли `psql` выполните, заменив `CHANGE_ME` на новый пароль:

```sql
CREATE USER samogon WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE samogon OWNER samogon;
\c samogon
GRANT ALL ON SCHEMA public TO samogon;
```

Проверьте подключение и выйдите:

```sql
\conninfo
\q
```

## 3. Каталоги статики и медиа

```bash
sudo mkdir -p /srv/data/samogon/static /srv/data/samogon/media
sudo chown -R deploy-user:deploy-user /srv/data/samogon
```

Добавьте два mount в сервис `nginx` файла
`/srv/compose/nginx/compose.yaml` рядом с mount диплома:

```yaml
      - /srv/data/samogon/static:/var/www/samogon/static:ro
      - /srv/data/samogon/media:/var/www/samogon/media:ro
```

Примените обновление Nginx-контейнера:

```bash
cd /srv/compose/nginx
docker compose up -d
```

## 4. Окружение приложения

Разместите файл `docker-compose.yml` из репозитория как
`/srv/compose/samogon/compose.yaml`. Настройки приложения хранятся отдельно:

```bash
cp .env.production.example /srv/config/env/samogon.env
nano /srv/config/env/samogon.env
```

Обязательно замените:

- `SECRET_KEY` — вывод `openssl rand -base64 48`;
- пароль в `DATABASE_URL` — на тот же, что использован для роли `samogon`;
- `REGISTRATION_INVITE_CODE` — вывод `openssl rand -hex 24`, только для
  приглашённых тестеров;
- SMTP-параметры, если приложение должно отправлять почту;
- `GITHUB_OAUTH_CLIENT_ID` и `GITHUB_OAUTH_CLIENT_SECRET` из GitHub OAuth App;
- `GOOGLE_OAUTH_CLIENT_ID` и `GOOGLE_OAUTH_CLIENT_SECRET` из Google OAuth client.
- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` и контактный `VAPID_SUBJECT` для Web Push.

Для SMTP включайте только один режим шифрования. Обычно это
`EMAIL_PORT=587`, `EMAIL_USE_TLS=1`, `EMAIL_USE_SSL=0`. Для Яндекс Почты с
SSL используйте `EMAIL_PORT=465`, `EMAIL_USE_TLS=0`, `EMAIL_USE_SSL=1` и
пароль приложения вместо основного пароля аккаунта.

Оставьте следующие значения как есть:

```dotenv
ALLOWED_HOSTS=app.example.invalid
CSRF_TRUSTED_ORIGINS=https://app.example.invalid
REDIS_URL=redis://redis:6379/0
OLLAMA_BASE_URL=http://ollama.internal:11434
OLLAMA_MODEL=samogon-semen-caretaker
OLLAMA_KEEP_ALIVE=-1
OLLAMA_TEMPERATURE=0.5
OLLAMA_NUM_PREDICT=120
BARTENDER_RESPONSE_MAX_LENGTH=360
REGISTRATION_INVITE_CODE=replace-with-a-long-random-invite-code
# Turnstile включается только когда заданы оба ключа.
TURNSTILE_SITE_KEY=
TURNSTILE_SECRET_KEY=
RATE_LIMIT_WINDOW_SECONDS=60
LOGIN_RATE_LIMIT=10
REGISTRATION_RATE_LIMIT=5
PASSWORD_RESET_RATE_LIMIT=5
MESSAGE_RATE_LIMIT=20
BARTENDER_RATE_LIMIT=5
REACTION_RATE_LIMIT=30
TYPING_RATE_LIMIT=60
SAMOGON_DATA_DIR=/srv/data/samogon
SAMOGON_IMAGE=registry.example.invalid/project/samogon:latest
ATTACHMENT_MAX_COUNT=3
ATTACHMENT_IMAGE_MAX_SIZE=5242880
ATTACHMENT_FILE_MAX_SIZE=2097152
ATTACHMENT_RATE_LIMIT=10
```

### GitHub и Google OAuth

Создайте отдельные production-приложения у провайдеров и укажите точные
callback URL:

```text
https://app.example.invalid/accounts/github/login/callback/
https://app.example.invalid/accounts/google/login/callback/
```

Для GitHub создайте OAuth App в `Settings → Developer settings → OAuth Apps`.
Homepage URL — `https://app.example.invalid/`, Authorization callback URL —
первый адрес выше. Для Google создайте OAuth 2.0 Client ID типа `Web
application`, добавьте `https://app.example.invalid` в Authorized JavaScript
origins, а второй адрес — в Authorized redirect URIs. Экран согласия Google
должен быть опубликован либо тестовый пользователь должен быть добавлен явно.

Секреты задаются только в `/srv/config/env/samogon.env`; в базе и репозитории
они не хранятся. Кнопка провайдера появляется только при наличии одновременно
ID и секрета. Для локальной проверки используйте отдельные OAuth-приложения с
callback на адрес локального приложения; production-секреты локально не копируйте.

Совпавший подтверждённый email не объединяет аккаунты автоматически. В таком
случае пользователь сначала входит обычным способом, затем подключает GitHub
или Google в профиле. Access и refresh tokens провайдеров не сохраняются.

### Web Push и VAPID

Создайте отдельную однострочную VAPID-пару на защищённой машине после установки
зависимостей проекта:

```bash
umask 077
poetry run python manage.py generate_vapid_keys > vapid.env
```

Перенесите обе строки из `vapid.env` в `samogon.env`, а в `VAPID_SUBJECT`
укажите рабочий `mailto:` администратора. Файл `vapid.env` и приватный ключ не
добавляйте в Git и журналы.
Перезапуск приложения применяет ключи; смена пары потребует повторной подписки
всех устройств.

Штатный GitHub Actions deploy также умеет выполнить первичную инициализацию:
если в `/srv/config/env/samogon.env` отсутствует хотя бы один VAPID-ключ, workflow
после загрузки образа создаёт пару непосредственно на сервере, записывает её с
правами исходного env-файла и использует настроенный публичный origin как
`VAPID_SUBJECT`. Уже настроенную пару workflow не заменяет, поэтому подписки
устройств сохраняются между релизами. Значения ключей в журнал Actions не
выводятся.

Push работает только в безопасном HTTPS-контексте. Проверьте вручную
установленную PWA в Android Chrome и поддерживаемые desktop Chrome/Firefox:
включение и отключение в профиле, доставку при закрытой вкладке и переход по
уведомлению в доступную комнату. На экране уведомления не должны появляться
текст личной реплики и имя отправителя.

После деплоя проверьте диагностический API. Публичный маршрут не содержит
секретов или персональных данных:

```bash
curl --fail https://app.example.invalid/api/v1/status/
```

В авторизованной PWA откройте `/api/v1/push/subscriptions/`: ответ должен
содержать только устройства текущего пользователя с непрозрачными `device_id`,
без endpoint, `p256dh` и `auth`. Для адресного теста отправьте JSON
`{"device_id":"<device_id>"}` методом POST на `/api/v1/push/self-test/` с
CSRF-заголовком текущей сессии. Ответ `status: accepted` означает лишь, что
push-служба приняла отправку; появление уведомления всё равно подтвердите на
реальном устройстве.

Рядом с `compose.yaml` создайте `.env` только с путями и образом (в нём нет
секретов):

```dotenv
SAMOGON_ENV_FILE=/srv/config/env/samogon.env
SAMOGON_IMAGE=registry.example.invalid/project/samogon:latest
```

Перед стартом проверьте связь с Ollama:

```bash
curl --fail http://ollama.internal:11434/api/tags
```

### Профиль Семёна-бармена на Gemma 3 4B

На VM с Ollama один раз создайте профиль. В репозитории уже лежит готовый
`deployment/ollama/Modelfile.semen-caretaker`. Системный промпт хранится только в
`chat/services/prompts/semen-caretaker.txt` и передаётся приложением, поэтому
образ персонажа не дублируется в модели:

```bash
ollama pull gemma3:4b
ollama create samogon-semen-caretaker \
  -f /path/to/samogon/deployment/ollama/Modelfile.semen-caretaker
bash /path/to/samogon/deployment/ollama/benchmark-api.sh \
  samogon-semen-gemma samogon-semen-caretaker
```

Сравните естественность русского языка, устойчивость роли и время ответа.
Qwen3 4B Instruct проверен как запасной вариант, но Gemma 3 4B выбрана для
беты за более естественную русскую речь и меньшую задержку после прогрева.
Production-профиль выбирается одной переменной `OLLAMA_MODEL`.

`OLLAMA_KEEP_ALIVE=-1` удерживает профиль в VRAM до перезапуска Ollama или
необходимости освободить память. `benchmark-api.sh` проверяет модель через тот
же API и с тем же системным промптом, что использует Django. Порт Ollama
по-прежнему оставьте доступным только во внутренней сети.

### Закрытая регистрация и лимиты

При `DEBUG=0` пустой `REGISTRATION_INVITE_CODE` отключает регистрацию. Тестер
вводит код в форме, но код нигде не хранится и не попадает в базу данных.

Для открытой регистрации создайте виджет Cloudflare Turnstile для
публичного домена приложения и задайте оба ключа в `samogon.env`. Публичный
`TURNSTILE_SITE_KEY` попадает только в HTML, а `TURNSTILE_SECRET_KEY` остаётся
на сервере. Без серверной проверки токена регистрация не проходит. Пока ключи
пустые, виджет отключён — это удобно для локальной разработки и закрытой беты.

Django использует общий Redis для лимитов: по умолчанию один IP может сделать
до 10 попыток входа, 5 регистраций и 5 запросов восстановления пароля за минуту,
один пользователь — до 20
сообщений и 5 обращений к Семёну за минуту. Nginx добавляет внешний барьер до
приложения. Значения меняются только в `/srv/config/env/samogon.env`.

## 5. Запуск контейнера приложения

```bash
cd /srv/compose/samogon
docker compose pull
docker compose up -d --no-build
docker compose ps
docker compose logs -f samogon-web
```

Контейнер применит миграции и соберёт статику перед запуском Daphne. Он не
открывает ни одного порта на хосте: Nginx найдёт его по имени `samogon-web` в
сети `infra`.

Ответы Семёна выполняет отдельный сервис `samogon-worker` из того же образа.
Перед первым деплоем версии с очередью обязательно обновите серверный
`compose.yaml` из репозитория. Проверка worker:

```bash
docker compose ps
docker compose logs -f samogon-worker
```

Worker использует Redis как брокер и PostgreSQL для статусов задач. Параметр
`SKIP_BOOTSTRAP=1` не даёт ему повторять миграции и `collectstatic`, а начальная
concurrency равна одному, чтобы не перегружать локальную Ollama.

### Защищённые вложения

Вложения чата хранятся в уже смонтированном каталоге
`/srv/data/samogon/media/chat/attachments`. Не публикуйте этот путь напрямую:
в конфиге Nginx из репозитория есть `internal`-location для него. Django
проверяет права пользователя, а затем передаёт файл Nginx внутренним заголовком
`X-Accel-Redirect`. После обновления конфига проверьте и перезагрузите Nginx:

```bash
docker exec nginx nginx -t
docker exec nginx nginx -s reload
```

В конфиге задан `client_max_body_size 20m`: этого достаточно для трёх
разрешённых изображений до 5 МБ с запасом на multipart-запрос. Не уменьшайте
его ниже суммарного лимита, иначе Nginx вернёт 413 до проверки Django.

### Модераторы и баны

После миграций контейнер создаёт группу `Moderators`. Суперпользователь в
`/admin/` назначает модератора так:

1. открывает пользователя;
2. включает только `Staff status`;
3. добавляет группу `Moderators`;
4. сохраняет изменения.

Суперпользователь — техническая учётная запись и не подключается к WebSocket
чата. Модератор видит в админке сообщения и журнал решений, может скрывать
сообщения и выдавать бан на сутки, неделю или навсегда. Текст сообщений и роли
пользователей модератор не редактирует.

## 6. Конфигурация существующего Nginx

После запуска `samogon-web` скопируйте файлы в уже смонтированный каталог
конфигураций. Первый объявляет зоны лимитов, второй использует их для входа,
регистрации и новых WebSocket-подключений:

```bash
cp deployment/nginx/samogon-rate-limits.conf \
  /srv/config/nginx/conf.d/samogon-rate-limits.conf
cp deployment/nginx/samogon-site.conf \
  /srv/config/nginx/conf.d/samogon-site.conf
docker exec nginx nginx -t
docker exec nginx nginx -s reload
```

Шаблон использует `/etc/letsencrypt`, поэтому сертификат автоматически
подхватится после перезагрузки Nginx. Контейнер `samogon-web` не должен
получать собственный опубликованный порт: весь трафик проходит только через
Nginx.

## 7. Боевая проверка

```bash
curl -I https://app.example.invalid/chat/
docker logs --tail 100 samogon-web
```

Дальше откройте сайт в двух браузерах и проверьте: регистрацию, общий чат,
WebSocket, личное сообщение, тайный столик, счётчик непрочитанного и ответ
`@Семён`.

## Локальный Docker-режим

Обычная разработка через `poetry run python manage.py runserver` остаётся
доступной. Чтобы проверить PostgreSQL, Redis и контейнерный запуск локально:

```bash
cp .env.docker.example .env.docker
docker compose -f docker-compose.local.yml up --build
```

Сайт откроется на локальном порту Django. Остановка с удалением тестовой БД:

```bash
docker compose -f docker-compose.local.yml down --volumes
```

## CI/CD

Pull request в `develop` запускает `.github/workflows/ci.yml`: Django checks,
тесты и Docker build без публикации образа и без доступа к серверу. После push
в `main` workflow `publish-deploy.yml` публикует
образ `project/samogon:latest` и тег SHA коммита, затем разворачивает SHA-тег
на настроенном хосте приложения.

## Обновление

```bash
cd /srv/compose/samogon
SAMOGON_IMAGE=registry.example.invalid/project/samogon:IMAGE_SHA \
  docker compose up -d --no-build
docker image prune -f
```

## Продление сертификата

Добавьте задание root `cron` (например, раз в неделю):

```cron
17 4 * * 1 docker run --rm -v /srv/data/certbot:/var/www/certbot -v /srv/data/letsencrypt:/etc/letsencrypt certbot/certbot renew --webroot -w /var/www/certbot --quiet && docker exec nginx nginx -s reload
```
