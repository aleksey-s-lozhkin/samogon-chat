#!/bin/sh
set -eu

if [ "${SKIP_BOOTSTRAP:-0}" != "1" ]; then
    # Docker DNS и PostgreSQL могут стать доступны чуть позже контейнера web.
    # Не превращаем краткий сетевой разрыв в бесконечный restart-loop.
    database_ready=0
    attempt=1
    while [ "$attempt" -le 30 ]; do
        if python -c '
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.db import connections
with connections["default"].cursor() as cursor:
    cursor.execute("SELECT 1")
'; then
            database_ready=1
            break
        fi
        echo "Database is unavailable; retrying bootstrap ($attempt/30)." >&2
        attempt=$((attempt + 1))
        sleep 2
    done
    if [ "$database_ready" -ne 1 ]; then
        echo "Database did not become available within 60 seconds." >&2
        exit 1
    fi

    # Миграции и статика должны быть готовы до приёма трафика Daphne.
    python manage.py migrate --noinput
    python manage.py setup_moderators
    python manage.py collectstatic --noinput
fi

exec "$@"
