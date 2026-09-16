# Проверка release candidate

Чек-лист выполняется на production-хосте до финального merge. Команды не
выводят пароли, cookies и содержимое сообщений в отчёты. Временная комната и
учётные записи нагрузки удаляются сразу после замера.

## 1. Резервная копия и пробное восстановление

Скрипт читает рабочую базу, сохраняет dump PostgreSQL и архив media, затем
восстанавливает dump во временный PostgreSQL-контейнер. Production-база при
этом не изменяется.

```bash
sudo install -d -m 700 /srv/backups/samogon
cd /srv/compose/samogon
bash /path/to/repository/scripts/verify_backup_restore.sh \
  --source-container postgres \
  --database samogon \
  --media-dir /srv/data/samogon/media \
  --output-dir /srv/backups/samogon
```

Успешный результат заканчивается строкой
`Backup and isolated restore verified`. После проверки нужно перенести dump,
media-архив и файл SHA-256 на отдельный защищённый носитель. Копия на том же
сервере не защищает от потери самого сервера.

## 2. Подготовка временных клиентов нагрузки

Команда создаёт закрытую комнату `release-audit`, 60 пользователей с
непригодными для входа паролями и короткоживущие Django-сессии. Файл сессий
создаётся с правами `0600`.

```bash
docker exec samogon-web python manage.py release_audit_accounts prepare \
  --credentials /tmp/release-audit-sessions.json \
  --count 60 \
  --room-slug release-audit \
  --confirm RELEASE-AUDIT-DATA

sudo docker cp \
  samogon-web:/tmp/release-audit-sessions.json \
  /srv/config/release-audit-sessions.json
sudo chmod 600 /srv/config/release-audit-sessions.json
```

## 3. Нагрузочный замер

Запускать генератор отдельным контейнером, чтобы его CPU/RAM не попали в
метрики `samogon-web`. Укажите точный SHA-образ текущего релиза вместо
`IMAGE_SHA`.

```bash
docker run --rm \
  --network infra \
  --entrypoint python \
  --volume /srv/config/release-audit-sessions.json:/run/audit-sessions.json:ro \
  IMAGE_SHA \
  /app/scripts/performance_audit.py \
  --base-url http://samogon-web:8000 \
  --host-header sam.pyconstrictor.ru \
  --forwarded-proto https \
  --room release-audit \
  --credentials /run/audit-sessions.json \
  --active-clients 30 \
  --idle-clients 30 \
  --hold-seconds 60 \
  --bartender-samples 3
```

Во втором терминале во время замера сохранить показатели контейнеров:

```bash
docker stats --no-stream samogon-web samogon-worker postgres redis
```

Отчёт должен содержать `status: ok`, 60 успешных подключений и p50/p95 для
HTTP, WebSocket-соединения, сообщений и Семёна. Результаты и снимок
`docker stats` сохраняются в журнале релиза. Если узкое место не подтверждено,
Redis-кэш истории и профилей перед релизом не добавляется.

## 4. Удаление тестовых данных

```bash
docker exec samogon-web python manage.py release_audit_accounts cleanup \
  --credentials /tmp/release-audit-sessions.json \
  --room-slug release-audit \
  --confirm RELEASE-AUDIT-DATA

sudo rm -- /srv/config/release-audit-sessions.json
```

После команд убедиться, что оба файла с сессиями исчезли. Они не размещаются
в каталоге media и не могут быть отданы веб-сервером.

## 5. Production smoke-test

Сначала выполнить безопасные проверки публичной границы:

```bash
python scripts/production_smoke.py --base-url https://sam.pyconstrictor.ru
```

Для проверки авторизованной страницы и WebSocket временно подготовить audit
сессии, как в пункте 2, и выполнить скрипт из сети `infra` либо передать ему
защищённый файл сессий:

```bash
python scripts/production_smoke.py \
  --base-url https://sam.pyconstrictor.ru \
  --room release-audit \
  --credentials /secure/path/audit-sessions.json
```

Затем вручную проверить в двух обычных аккаунтах:

1. GitHub OAuth и Google OAuth с возвратом в исходную комнату.
2. Регистрацию с Turnstile и восстановление пароля.
3. Общую комнату, личное сообщение и закрытую беседу.
4. Ответ Семёна и состояние его фонового задания.
5. Вложение изображения и документа, реакцию, ответ и жалобу.
6. Web Push при заблокированном экране Android и iPhone PWA.
7. Установку/повторный запуск PWA и работу мобильной клавиатуры.
8. `/health/live/`, `/health/ready/`, состояние web/worker и последние логи.

## 6. VoiceOver

VoiceOver — встроенное чтение экрана Apple. На iPhone оно включается в
`Настройки → Универсальный доступ → VoiceOver`; на Mac — сочетанием
`Command+F5`.

Не глядя на экран, пройти вход, регистрацию и восстановление пароля. Проверить,
что VoiceOver называет каждое поле и кнопку, сообщает об ошибках, переводит
фокус к первой ошибке и позволяет исправить её. Отдельно проверить показ
пароля, Caps Lock, переключение вход/регистрация и OAuth-кнопки. Результат
записать в журнал релиза: устройство, версия ОС, сценарий и найденные дефекты.
