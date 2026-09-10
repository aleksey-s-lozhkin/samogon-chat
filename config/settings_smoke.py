"""Изолированные настройки автоматического браузерного smoke-test."""

import os

from .settings import *  # noqa: F403


DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ["SAMOGON_SMOKE_DB"],
    },
}
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REDIS_URL = ""

