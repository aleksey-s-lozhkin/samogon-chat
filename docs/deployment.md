# Production-деплой Самогона

## Реальная схема сервера

Samogon запускается на VM `infra-dev` (`192.168.0.123`) рядом с уже
работающими контейнерами. Новые PostgreSQL, Redis и Nginx не создаются.

```text
sam.pyconstrictor.ru → nginx → samogon-web:8000
                                  ├── postgres:5432
                                  ├── redis:6379
                                  └── Ollama 192.168.0.78:11434
```

Все контейнеры находятся в существующей внешней Docker-сети `infra`.

## 1. Перед запуском

DNS `sam.pyconstrictor.ru` должен указывать на публичный IP маршрутизатора,
а порты 80 и 443 — перенаправляться на `infra-dev`. Для TLS используется
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
  -d sam.pyconstrictor.ru --email YOUR_EMAIL \
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
sudo chown -R alserloz:alserloz /srv/data/samogon
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

Для SMTP включайте только один режим шифрования. Обычно это
`EMAIL_PORT=587`, `EMAIL_USE_TLS=1`, `EMAIL_USE_SSL=0`. Для Яндекс Почты с
SSL используйте `EMAIL_PORT=465`, `EMAIL_USE_TLS=0`, `EMAIL_USE_SSL=1` и
пароль приложения вместо основного пароля аккаунта.

Оставьте следующие значения как есть:

```dotenv
ALLOWED_HOSTS=sam.pyconstrictor.ru
CSRF_TRUSTED_ORIGINS=https://sam.pyconstrictor.ru
REDIS_URL=redis://redis:6379/0
OLLAMA_BASE_URL=http://192.168.0.78:11434
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
SAMOGON_IMAGE=docker.io/alserloz/samogon_chat:latest
ATTACHMENT_MAX_COUNT=3
ATTACHMENT_IMAGE_MAX_SIZE=5242880
ATTACHMENT_FILE_MAX_SIZE=2097152
ATTACHMENT_RATE_LIMIT=10
```

### GitHub и Google OAuth

Создайте отдельные production-приложения у провайдеров и укажите точные
callback URL:

```text
https://sam.pyconstrictor.ru/accounts/github/login/callback/
https://sam.pyconstrictor.ru/accounts/google/login/callback/
```

Для GitHub создайте OAuth App в `Settings → Developer settings → OAuth Apps`.
Homepage URL — `https://sam.pyconstrictor.ru/`, Authorization callback URL —
первый адрес выше. Для Google создайте OAuth 2.0 Client ID типа `Web
application`, добавьте `https://sam.pyconstrictor.ru` в Authorized JavaScript
origins, а второй адрес — в Authorized redirect URIs. Экран согласия Google
должен быть опубликован либо тестовый пользователь должен быть добавлен явно.

Секреты задаются только в `/srv/config/env/samogon.env`; в базе и репозитории
они не хранятся. Кнопка провайдера появляется только при наличии одновременно
ID и секрета. Для локальной проверки используйте отдельные OAuth-приложения с
callback на `http://127.0.0.1:8000`; production-секреты локально не копируйте.

Совпавший подтверждённый email не объединяет аккаунты автоматически. В таком
случае пользователь сначала входит обычным способом, затем подключает GitHub
или Google в профиле. Access и refresh tokens провайдеров не сохраняются.

Рядом с `compose.yaml` создайте `.env` только с путями и образом (в нём нет
секретов):

```dotenv
SAMOGON_ENV_FILE=/srv/config/env/samogon.env
SAMOGON_IMAGE=docker.io/alserloz/samogon_chat:latest
```

Перед стартом проверьте связь с Ollama:

```bash
curl --fail http://192.168.0.78:11434/api/tags
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
`sam.pyconstrictor.ru` и задайте оба ключа в `samogon.env`. Публичный
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
cp deployment/nginx/sam.pyconstrictor.ru.conf \
  /srv/config/nginx/conf.d/sam.pyconstrictor.ru.conf
docker exec nginx nginx -t
docker exec nginx nginx -s reload
```

Шаблон использует `/etc/letsencrypt`, поэтому сертификат автоматически
подхватится после перезагрузки Nginx. Контейнер `samogon-web` не должен
получать собственный опубликованный порт: весь трафик проходит только через
Nginx.

## 7. Боевая проверка

```bash
curl -I https://sam.pyconstrictor.ru/chat/
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

Сайт откроется на `http://127.0.0.1:8000`. Остановка с удалением тестовой БД:

```bash
docker compose -f docker-compose.local.yml down --volumes
```

## CI/CD

Pull request в `develop` запускает `.github/workflows/ci.yml`: Django checks,
тесты и Docker build без публикации образа и без доступа к серверу. После push
в `main` workflow `publish-deploy.yml` публикует
`alserloz/samogon_chat:latest` и тег SHA коммита, затем разворачивает SHA-тег
на `infra-dev`.

## Обновление

```bash
cd /srv/compose/samogon
SAMOGON_IMAGE=docker.io/alserloz/samogon_chat:IMAGE_SHA \
  docker compose up -d --no-build
docker image prune -f
```

## Продление сертификата

Добавьте задание root `cron` (например, раз в неделю):

```cron
17 4 * * 1 docker run --rm -v /srv/data/certbot:/var/www/certbot -v /srv/data/letsencrypt:/etc/letsencrypt certbot/certbot renew --webroot -w /var/www/certbot --quiet && docker exec nginx nginx -s reload
```
