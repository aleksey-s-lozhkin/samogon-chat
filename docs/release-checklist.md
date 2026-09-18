# Проверка release candidate

Чек-лист выполняется на production-хосте один раз для задеплоенного кандидата.
Секреты, cookies и тексты сообщений не копируются в отчёт. Не повторяйте
успешный этап без изменения затрагивающего его кода или конфигурации.

## 0. Быстрый preflight

Зафиксируйте точный образ и убедитесь, что сервисы готовы:

```bash
docker inspect samogon-web --format '{{.Config.Image}}'
docker ps --format 'table {{.Names}}\t{{.Status}}' \
  --filter name=samogon-web \
  --filter name=samogon-worker \
  --filter name=postgres \
  --filter name=redis
curl -fsS https://sam.pyconstrictor.ru/health/live/
curl -fsS https://sam.pyconstrictor.ru/health/ready/
```

Если readiness не возвращает `status: ok`, остальные проверки не запускаются.

## 1. Backup и пробное восстановление

Сценарий читает production-базу, сохраняет dump PostgreSQL и media, затем
восстанавливает dump в отдельный временный PostgreSQL-контейнер. Production-БД
не изменяется.

Получите сценарий из уже запущенного образа, чтобы не держать checkout
репозитория на сервере:

```bash
docker cp \
  samogon-web:/app/scripts/verify_backup_restore.sh \
  /tmp/verify-samogon-backup.sh
chmod 700 /tmp/verify-samogon-backup.sh
sudo install -d -m 700 /srv/backups/samogon
sudo /tmp/verify-samogon-backup.sh \
  --source-container postgres \
  --database samogon \
  --media-dir /srv/data/samogon/media \
  --output-dir /srv/backups/samogon
```

Успех заканчивается строкой `Backup and isolated restore verified`. Dump,
media-архив и SHA-256 нужно перенести на отдельный защищённый носитель: копия
на production-хосте не защищает от потери самого сервера.

## 2. Временные клиенты нагрузки

Файл `/tmp/release-audit-sessions.json` находится внутри контейнера и исчезает
при каждом deploy. Постоянная защищённая копия между deploy хранится только на
хосте: `/srv/config/release-audit-sessions.json` с правами `0600`.

### Если audit-комната ещё не создавалась

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

Audit-пользователи имеют непригодные для входа пароли. Созданные сессии
действуют четыре часа.

### Если после подготовки выполнялся deploy

Не создавайте пользователей повторно. Верните защищённый файл с хоста в новый
контейнер и проверьте сессии:

```bash
sudo docker cp \
  /srv/config/release-audit-sessions.json \
  samogon-web:/tmp/release-audit-sessions.json

docker exec samogon-web python manage.py shell -c \
'import json; from django.contrib.sessions.models import Session; from django.utils import timezone; d=json.load(open("/tmp/release-audit-sessions.json")); keys=[x["sessionid"] for x in d]; print("Всего:",len(keys),"действующих:",Session.objects.filter(session_key__in=keys,expire_date__gt=timezone.now()).count())'
```

Продолжайте только при `Всего: 60 действующих: 60`. Если файл отсутствует на
хосте или сессии истекли, сначала выполните cleanup по сохранившемуся файлу,
затем один раз повторите prepare.

## 3. Нагрузка 30 + 30

Нагрузочный контейнер запускается отдельно, чтобы его CPU/RAM не попали в
метрики приложения. Вместо `CURRENT_IMAGE` подставьте точный результат команды
из preflight. Семён в этот замер не включается: модель проверяется отдельным
коротким запуском и не должна маскировать пропускную способность чата.

```bash
docker run --rm \
  --network infra \
  --entrypoint python \
  --volume /srv/config/release-audit-sessions.json:/run/audit-sessions.json:ro \
  CURRENT_IMAGE \
  /app/scripts/performance_audit.py \
  --base-url http://samogon-web:8000 \
  --host-header sam.pyconstrictor.ru \
  --forwarded-proto https \
  --room release-audit \
  --credentials /run/audit-sessions.json \
  --active-clients 30 \
  --idle-clients 30 \
  --connection-interval 0.25 \
  --hold-seconds 60 \
  --bartender-samples 0 \
  --timeout 30
```

Во втором терминале во время удержания соединений:

```bash
docker stats --no-stream samogon-web samogon-worker postgres redis nginx
docker inspect samogon-web \
  --format 'restarts={{.RestartCount}} oom={{.State.OOMKilled}} health={{.State.Health.Status}}'
```

Критерий прохождения: `status: ok`, 60 подключений, `errors: []`, без restart,
OOM и потери readiness. p50/p95 и снимок ресурсов сохраняются в журнале релиза.
Если тест не прошёл, не повторяйте его вслепую: один раз сохраните JSON ошибки,
`docker logs --since 10m samogon-web` и `docker stats`, затем исправляйте
конкретный этап.

## 4. Отдельная проверка Семёна

После успешных 30 + 30 выполните короткий запуск одним клиентом:

```bash
docker run --rm \
  --network infra \
  --entrypoint python \
  --volume /srv/config/release-audit-sessions.json:/run/audit-sessions.json:ro \
  CURRENT_IMAGE \
  /app/scripts/performance_audit.py \
  --base-url http://samogon-web:8000 \
  --host-header sam.pyconstrictor.ru \
  --forwarded-proto https \
  --room release-audit \
  --credentials /run/audit-sessions.json \
  --active-clients 1 \
  --idle-clients 0 \
  --connection-interval 0 \
  --hold-seconds 0 \
  --bartender-samples 3 \
  --bartender-timeout 180
```

## 5. Production smoke

Публичную и авторизованную границу проверяйте тем же образом релиза:

```bash
docker run --rm \
  --network infra \
  --entrypoint python \
  --volume /srv/config/release-audit-sessions.json:/run/audit-sessions.json:ro \
  CURRENT_IMAGE \
  /app/scripts/production_smoke.py \
  --base-url https://sam.pyconstrictor.ru \
  --room release-audit \
  --credentials /run/audit-sessions.json
```

Затем в двух обычных аккаунтах проверьте только критический маршрут:

1. Общая, личная и закрытая беседа.
2. Ответ Семёна и состояние фонового задания.
3. Изображение или документ, одно аудиосообщение, реакция и ответ.
4. Одно Web Push на реальном устройстве.
5. Мобильная клавиатура: composer остаётся видимым после ввода и закрытия.
6. Последние логи web/worker не содержат новой необработанной ошибки.

OAuth, Turnstile и полная матрица Push повторяются только после изменения их
кода или production-конфигурации.

## 6. Cleanup

После всех проверок файл с хоста нужно вернуть в контейнер, потому что `/tmp`
мог исчезнуть при deploy:

```bash
sudo docker cp \
  /srv/config/release-audit-sessions.json \
  samogon-web:/tmp/release-audit-sessions.json

docker exec samogon-web python manage.py release_audit_accounts cleanup \
  --credentials /tmp/release-audit-sessions.json \
  --room-slug release-audit \
  --confirm RELEASE-AUDIT-DATA

sudo rm -- /srv/config/release-audit-sessions.json
```

Cleanup удаляет только комнату `release-audit`, временных audit-пользователей,
их сессии и защищённый файл. После него production smoke выполняется только в
публичном режиме либо с обычным тестовым аккаунтом.

## 7. VoiceOver

Один раз до открытой беты включите VoiceOver на iPhone или Mac и без взгляда
на экран пройдите вход, регистрацию и восстановление пароля. Проверяются
названия полей и кнопок, сообщение об ошибке, переход к первой ошибке,
исправление значения, показ пароля и OAuth-кнопки. Найденный блокирующий дефект
исправляется до релиза; косметический заносится в post-beta backlog.
