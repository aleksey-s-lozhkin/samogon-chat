#!/bin/sh
# Резервная копия Самогона: база, медиа и конфигурация.
#
# Запускается на хосте, где работает docker compose. Ничего не удаляет, кроме
# собственных старых копий. Секреты попадают в архив, поэтому архивы шифруются
# и ключ должен лежать отдельно от них.
set -eu

COMPOSE_DIR=${COMPOSE_DIR:-/srv/samogon}
BACKUP_DIR=${BACKUP_DIR:-/srv/backups/samogon}
KEEP_DAYS=${KEEP_DAYS:-14}
KEY_FILE=${KEY_FILE:-/srv/config/samogon/backup.key}
DATA_DIR=${SAMOGON_DATA_DIR:-/srv/data/samogon}
ENV_FILE=${SAMOGON_ENV_FILE:-$COMPOSE_DIR/.env.production}
NGINX_CONF_DIR=${NGINX_CONF_DIR:-/srv/config/nginx/conf.d}
DB_CONTAINER=${DB_CONTAINER:-postgres}
WEB_CONTAINER=${WEB_CONTAINER:-samogon-web}
RCLONE_REMOTE=${RCLONE_REMOTE:-}
STAMP=$(date +%Y-%m-%d-%H%M)

log() {
    printf '%s %s\n' "$(date +%H:%M:%S)" "$1"
}

fail() {
    printf 'Ошибка: %s\n' "$1" >&2
    exit 1
}

[ -r "$KEY_FILE" ] || fail "нет ключа шифрования $KEY_FILE (создайте: openssl rand -base64 32 > $KEY_FILE && chmod 600 $KEY_FILE)"
command -v openssl >/dev/null 2>&1 || fail "нужен openssl"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Имя базы берём из настроек приложения: там уже разобран DATABASE_URL.
# Переменная DATABASE_NAME позволяет работать, когда веб-контейнер недоступен.
if [ -z "${DATABASE_NAME:-}" ]; then
    DATABASE_NAME=$(docker exec "$WEB_CONTAINER" python -c '
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
print(settings.DATABASES["default"]["NAME"])
') || fail "не удалось определить имя базы"
fi
[ -n "$DATABASE_NAME" ] || fail "пустое имя базы"

# В официальном образе Postgres локальные подключения идут через trust,
# поэтому пароль не нужен и в аргументах процесса не светится.
PG_SUPERUSER=$(docker exec "$DB_CONTAINER" sh -c 'printf %s "${POSTGRES_USER:-postgres}"') \
    || fail "не удалось определить пользователя Postgres"

encrypt() {
    # Аргументы: исходный файл, имя готового архива.
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -pass "file:$KEY_FILE" \
        -in "$1" -out "$2"
    rm -f "$1"
}

log "Дамп базы $DATABASE_NAME"
DUMP_FILE="$BACKUP_DIR/db-$STAMP.dump"
docker exec "$DB_CONTAINER" pg_dump -U "$PG_SUPERUSER" -d "$DATABASE_NAME" -Fc > "$DUMP_FILE" \
    || fail "pg_dump не справился"
[ -s "$DUMP_FILE" ] || fail "дамп получился пустым"
encrypt "$DUMP_FILE" "$DUMP_FILE.enc"

log "Архив медиа из $DATA_DIR/media"
MEDIA_FILE="$BACKUP_DIR/media-$STAMP.tar.gz"
if [ -d "$DATA_DIR/media" ]; then
    tar -czf "$MEDIA_FILE" -C "$DATA_DIR" media
    encrypt "$MEDIA_FILE" "$MEDIA_FILE.enc"
else
    log "Каталог медиа не найден, пропускаю"
fi

log "Архив конфигурации"
CONFIG_FILE="$BACKUP_DIR/config-$STAMP.tar.gz"
have_env=0
have_nginx=0
[ -f "$ENV_FILE" ] && have_env=1
[ -d "$NGINX_CONF_DIR" ] && have_nginx=1

if [ "$have_env" -eq 1 ] && [ "$have_nginx" -eq 1 ]; then
    tar -czf "$CONFIG_FILE" \
        -C "$(dirname "$ENV_FILE")" "$(basename "$ENV_FILE")" \
        -C "$(dirname "$NGINX_CONF_DIR")" "$(basename "$NGINX_CONF_DIR")"
    encrypt "$CONFIG_FILE" "$CONFIG_FILE.enc"
elif [ "$have_env" -eq 1 ]; then
    tar -czf "$CONFIG_FILE" -C "$(dirname "$ENV_FILE")" "$(basename "$ENV_FILE")"
    encrypt "$CONFIG_FILE" "$CONFIG_FILE.enc"
elif [ "$have_nginx" -eq 1 ]; then
    tar -czf "$CONFIG_FILE" -C "$(dirname "$NGINX_CONF_DIR")" "$(basename "$NGINX_CONF_DIR")"
    encrypt "$CONFIG_FILE" "$CONFIG_FILE.enc"
else
    log "Нечего архивировать в конфигурации"
fi

if [ -n "$RCLONE_REMOTE" ]; then
    if command -v rclone >/dev/null 2>&1; then
        log "Копия за пределы хоста: $RCLONE_REMOTE"
        rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" \
            --include "*-$STAMP.*.enc" \
            || log "Копирование не удалось, локальные архивы на месте"
    else
        log "rclone не установлен, копия за пределы хоста пропущена"
    fi
fi

log "Ротация: удаляю архивы старше $KEEP_DAYS дней"
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.enc' -mtime "+$KEEP_DAYS" -print -delete

log "Готово"
ls -lh "$BACKUP_DIR" | tail -n +2 | tail -8
