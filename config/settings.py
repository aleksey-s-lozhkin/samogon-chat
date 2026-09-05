import os
from pathlib import Path
from urllib.parse import unquote, urlparse

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# Локальная разработка остаётся простой, а production получает секрет из .env.
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-local-development-only",
)
DEBUG = os.getenv("DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]


# Application definition

INSTALLED_APPS = [
    'daphne',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'channels',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.github',
    'allauth.socialaccount.providers.google',
    'chat',
    'users',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'config.middleware.BannedUserMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'users.context_processors.turnstile',
                'users.context_processors.oauth_providers',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

database_url = os.getenv("DATABASE_URL")
if database_url:
    parsed_database_url = urlparse(database_url)
    if parsed_database_url.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL должен использовать схему postgresql://")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed_database_url.path.lstrip("/"),
            "USER": unquote(parsed_database_url.username or ""),
            "PASSWORD": unquote(parsed_database_url.password or ""),
            "HOST": parsed_database_url.hostname or "localhost",
            "PORT": str(parsed_database_url.port or 5432),
            "CONN_MAX_AGE": 60,
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        },
    }


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

# Русская локаль применяется и к встроенной административной панели Django.
LANGUAGE_CODE = 'ru'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Лимиты вложений держим рядом с настройками хранилища, чтобы их было видно при деплое.
ATTACHMENT_MAX_COUNT = int(os.getenv("ATTACHMENT_MAX_COUNT", "3"))
ATTACHMENT_IMAGE_MAX_SIZE = int(
    os.getenv("ATTACHMENT_IMAGE_MAX_SIZE", str(5 * 1024 * 1024))
)
ATTACHMENT_FILE_MAX_SIZE = int(
    os.getenv("ATTACHMENT_FILE_MAX_SIZE", str(2 * 1024 * 1024))
)
ATTACHMENT_RATE_LIMIT = int(os.getenv("ATTACHMENT_RATE_LIMIT", "10"))

STATICFILES_DIRS = [
    BASE_DIR / "static",
]


# Почтовый вывод в консоль удобен локально; production использует SMTP.
EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "0") == "1"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@localhost")

REDIS_URL = os.getenv("REDIS_URL")

if REDIS_URL:
    # Один Redis хранит Channels-события и общие для всех Daphne лимиты.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "samogon-local",
        },
    }

REGISTRATION_INVITE_CODE = os.getenv("REGISTRATION_INVITE_CODE", "")
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "10"))
REGISTRATION_RATE_LIMIT = int(os.getenv("REGISTRATION_RATE_LIMIT", "5"))
PASSWORD_RESET_RATE_LIMIT = int(os.getenv("PASSWORD_RESET_RATE_LIMIT", "5"))
MESSAGE_RATE_LIMIT = int(os.getenv("MESSAGE_RATE_LIMIT", "20"))
BARTENDER_RATE_LIMIT = int(os.getenv("BARTENDER_RATE_LIMIT", "5"))
REACTION_RATE_LIMIT = int(os.getenv("REACTION_RATE_LIMIT", "30"))
TYPING_RATE_LIMIT = int(os.getenv("TYPING_RATE_LIMIT", "60"))
PUSH_SELF_TEST_RATE_LIMIT = int(os.getenv("PUSH_SELF_TEST_RATE_LIMIT", "3"))
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:noreply@localhost")
WEB_PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Ollama runs on a separate machine in the local network.  Keeping this in an
# environment variable lets local development and production use different
# hosts without changing application code.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.78:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "samogon-semen-caretaker")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "20"))
OLLAMA_KEEP_ALIVE_RAW = os.getenv("OLLAMA_KEEP_ALIVE", "-1")
try:
    # Число -1 сообщает Ollama не выгружать модель из памяти.
    OLLAMA_KEEP_ALIVE = int(OLLAMA_KEEP_ALIVE_RAW)
except ValueError:
    # Строковые интервалы вроде "10m" Ollama также принимает.
    OLLAMA_KEEP_ALIVE = OLLAMA_KEEP_ALIVE_RAW
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.5"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "120"))
BARTENDER_RESPONSE_MAX_LENGTH = int(
    os.getenv("BARTENDER_RESPONSE_MAX_LENGTH", "360")
)
BARTENDER_USERNAME = os.getenv("BARTENDER_USERNAME", "semen")

# Метрики Семёна не содержат текст сообщений и нужны для диагностики Ollama.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "chat.services.bartender": {
            "handlers": ["console"],
            "level": os.getenv("BARTENDER_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}

# Turnstile включается только при наличии обеих ключей.
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
TURNSTILE_ENABLED = bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

AUTH_USER_MODEL = 'users.User'

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = "/chat/"
ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_REQUIRED = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_ADAPTER = "users.adapters.SamogonSocialAccountAdapter"

GITHUB_OAUTH_CLIENT_ID = os.getenv("GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_OAUTH_CLIENT_SECRET = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APPS": [
            {
                "client_id": GITHUB_OAUTH_CLIENT_ID,
                "secret": GITHUB_OAUTH_CLIENT_SECRET,
                "key": "",
            }
        ],
        "SCOPE": ["user:email"],
        "VERIFIED_EMAIL": True,
    },
    "google": {
        "APPS": [
            {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "key": "",
            }
        ],
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
        "VERIFIED_EMAIL": True,
    },
}

if not DEBUG:
    # TLS завершается на Nginx, поэтому Django доверяет заголовку прокси.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    X_FRAME_OPTIONS = "DENY"
    STORAGES = {
        # Аватары и другие пользовательские файлы сохраняются в MEDIA_ROOT.
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": (
                "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
            ),
        },
    }
