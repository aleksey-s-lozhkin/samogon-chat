#!/bin/sh
set -eu

# Миграции и статика должны быть готовы до приёма трафика Daphne.
python manage.py migrate --noinput
python manage.py collectstatic --noinput

exec "$@"
