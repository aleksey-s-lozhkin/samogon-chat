#!/bin/sh
# Проверка и восстановление резервных копий Самогона.
#
# Роли двух инструментов в проекте:
#   scripts/verify_backup_restore.sh — приёмочная проверка release candidate:
#        снимает свежий дамп с боевой базы и сравнивает счётчики таблиц
#        «источник против восстановленного». Живёт внутри образа.
#   этот скрипт — эксплуатация: проверяет уже СДЕЛАННЫЙ архив (в том числе
#        зашифрованный и лежащий неделю) и умеет из него восстановить. Проверка
#        разворачивает дамп в одноразовый контейнер Postgres, поэтому общий
#        сервер баз данных на хосте не затрагивается.
#
# Примеры:
#   samogon-restore.sh --list
#   samogon-restore.sh --verify                       # последняя копия
#   samogon-restore.sh --verify --stamp 2026-09-30-0315
#   samogon-restore.sh --restore --stamp 2026-09-30-0315 --yes-i-know
set -eu

BACKUP_DIR=${BACKUP_DIR:-/srv/backups/samogon}
KEY_FILE=${KEY_FILE:-/srv/config/samogon/backup.key}
DATA_DIR=${SAMOGON_DATA_DIR:-/srv/data/samogon}
DB_CONTAINER=${DB_CONTAINER:-postgres}
WEB_CONTAINER=${WEB_CONTAINER:-samogon-web}
WORKER_CONTAINER=${WORKER_CONTAINER:-samogon-worker}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-}

MODE=verify
STAMP=""
CONFIRMED=0

while [ $# -gt 0 ]; do
    case "$1" in
        --list) MODE=list ;;
        --verify) MODE=verify ;;
        --restore) MODE=restore ;;
        --yes-i-know) CONFIRMED=1 ;;
        --stamp) shift; STAMP=${1:-} ;;
        --dir) shift; BACKUP_DIR=${1:-$BACKUP_DIR} ;;
        --postgres-image) shift; POSTGRES_IMAGE=${1:-$POSTGRES_IMAGE} ;;
        *) printf 'Неизвестный аргумент: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

log() {
    printf '%s %s\n' "$(date +%H:%M:%S)" "$1"
}

fail() {
    printf 'Ошибка: %s\n' "$1" >&2
    exit 1
}

[ -d "$BACKUP_DIR" ] || fail "нет каталога копий $BACKUP_DIR"

if [ "$MODE" = "list" ]; then
    printf 'Копии в %s:\n' "$BACKUP_DIR"
    ls -lh "$BACKUP_DIR" | tail -n +2
    exit 0
fi

# Если метка не задана, берём последнюю копию базы по времени изменения файла.
if [ -z "$STAMP" ]; then
    latest=$(ls -1t "$BACKUP_DIR"/db-*.dump.enc 2>/dev/null | head -1 || true)
    [ -n "$latest" ] || fail "в $BACKUP_DIR нет ни одной копии базы"
    STAMP=$(basename "$latest" | sed 's/^db-//; s/\.dump\.enc$//')
fi

DUMP="$BACKUP_DIR/db-$STAMP.dump.enc"
MEDIA="$BACKUP_DIR/media-$STAMP.tar.gz.enc"
[ -f "$DUMP" ] || fail "нет дампа базы для метки $STAMP"
[ -r "$KEY_FILE" ] || fail "нет ключа шифрования $KEY_FILE"

TMP_DIR=$(mktemp -d)
RESTORE_CONTAINER=""

cleanup() {
    if [ -n "$RESTORE_CONTAINER" ]; then
        docker rm -f "$RESTORE_CONTAINER" >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

decrypt() {
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
        -pass "file:$KEY_FILE" -in "$1" -out "$2"
}

log "Расшифровываю дамп $STAMP"
decrypt "$DUMP" "$TMP_DIR/db.dump" || fail "не удалось расшифровать дамп (проверьте ключ $KEY_FILE)"
[ -s "$TMP_DIR/db.dump" ] || fail "дамп после расшифровки пуст"

PG_SUPERUSER=$(docker exec "$DB_CONTAINER" sh -c 'printf %s "${POSTGRES_USER:-postgres}"') \
    || fail "не удалось определить пользователя Postgres"

if [ "$MODE" = "restore" ]; then
    [ "$CONFIRMED" -eq 1 ] || fail "восстановление перезаписывает боевую базу: добавьте --yes-i-know"

    if [ -z "${DATABASE_NAME:-}" ]; then
        DATABASE_NAME=$(docker exec "$WEB_CONTAINER" python -c '
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings
print(settings.DATABASES["default"]["NAME"])
') || fail "не удалось определить имя базы"
    fi

    printf 'Восстанавливаю %s в базу %s. Текущие данные будут перезаписаны. Продолжить? [y/N] ' "$STAMP" "$DATABASE_NAME"
    read -r answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *) fail "отменено" ;;
    esac

    log "Останавливаю приложение, чтобы никто не писал в базу"
    docker stop "$WEB_CONTAINER" "$WORKER_CONTAINER" >/dev/null

    log "Восстанавливаю базу"
    docker exec -i "$DB_CONTAINER" pg_restore \
        -U "$PG_SUPERUSER" -d "$DATABASE_NAME" --clean --if-exists --no-owner \
        < "$TMP_DIR/db.dump" || log "pg_restore завершился с замечаниями, проверьте вывод выше"

    if [ -f "$MEDIA" ] && [ -d "$DATA_DIR" ]; then
        log "Раскладываю медиа"
        decrypt "$MEDIA" "$TMP_DIR/media.tar.gz"
        mv "$DATA_DIR/media" "$DATA_DIR/media.before-restore-$STAMP"
        tar -xzf "$TMP_DIR/media.tar.gz" -C "$DATA_DIR"
        log "Прежний каталог медиа сохранён как media.before-restore-$STAMP"
    fi

    log "Запускаю приложение"
    docker start "$WEB_CONTAINER" "$WORKER_CONTAINER" >/dev/null
    log "Готово. Проверьте сайт и логи контейнеров."
    exit 0
fi

# --- Безопасная проверка -----------------------------------------------------
# Одноразовый контейнер: общий Postgres на сервере не затрагивается, а неудача
# на середине не оставляет мусора в рабочем кластере.
if [ -z "$POSTGRES_IMAGE" ]; then
    postgres_major=$(docker exec "$DB_CONTAINER" psql -U "$PG_SUPERUSER" -At \
        -c "SHOW server_version_num" | cut -c1-2)
    case "$postgres_major" in
        ''|*[!0-9]*) fail "не удалось определить версию Postgres" ;;
    esac
    POSTGRES_IMAGE="postgres:${postgres_major}"
fi

RESTORE_CONTAINER="samogon-verify-$STAMP-$$"
restore_password=$(openssl rand -hex 24)

log "Поднимаю временный Postgres ($POSTGRES_IMAGE) для проверки"
docker run --detach \
    --name "$RESTORE_CONTAINER" \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=2g \
    --env POSTGRES_PASSWORD="$restore_password" \
    --env POSTGRES_DB=samogon_verify \
    "$POSTGRES_IMAGE" >/dev/null || fail "не удалось запустить временный Postgres"

ready=0
attempt=1
while [ "$attempt" -le 60 ]; do
    if docker exec "$RESTORE_CONTAINER" \
        pg_isready --username postgres --dbname samogon_verify >/dev/null 2>&1; then
        ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 1
done
[ "$ready" -eq 1 ] || fail "временный Postgres не поднялся за 60 секунд"

log "Разворачиваю дамп"
docker cp "$TMP_DIR/db.dump" "$RESTORE_CONTAINER:/tmp/samogon.dump" >/dev/null
docker exec "$RESTORE_CONTAINER" \
    pg_restore --exit-on-error --no-owner --no-acl \
    --username postgres --dbname samogon_verify /tmp/samogon.dump \
    || fail "дамп не разворачивается: копия повреждена"

log "Читаю восстановленную базу"
docker exec "$RESTORE_CONTAINER" \
    psql --username postgres --dbname samogon_verify \
    --no-align --tuples-only --field-separator=': ' -c "
SELECT 'пользователи', count(*) FROM users_user
UNION ALL SELECT 'беседы', count(*) FROM chat_room
UNION ALL SELECT 'сообщения', count(*) FROM chat_message
UNION ALL SELECT 'вложения', count(*) FROM chat_attachment
UNION ALL SELECT 'заметки', count(*) FROM chat_note
UNION ALL SELECT 'миграции', count(*) FROM django_migrations
ORDER BY 1;
" || fail "не удалось прочитать восстановленную базу"

if [ -f "$MEDIA" ]; then
    log "Проверяю архив медиа"
    decrypt "$MEDIA" "$TMP_DIR/media.tar.gz" || fail "не удалось расшифровать архив медиа"
    tar -tzf "$TMP_DIR/media.tar.gz" >/dev/null || fail "архив медиа повреждён"
    mkdir -p "$TMP_DIR/media-check"
    tar -xzf "$TMP_DIR/media.tar.gz" -C "$TMP_DIR/media-check"
    files=$(find "$TMP_DIR/media-check" -type f | wc -l | tr -d ' ')
    size=$(du -sh "$TMP_DIR/media-check" | cut -f1)
    printf 'медиа: файлов %s, объём %s\n' "$files" "$size"
else
    log "Архива медиа для этой метки нет"
fi

log "Копия разворачивается и читается. Временный контейнер будет удалён."
