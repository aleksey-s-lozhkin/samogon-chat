from django.apps import AppConfig


class UsersConfig(AppConfig):
    verbose_name = "Пользователи"
    name = 'users'

    def ready(self):
        from . import signals  # noqa: F401
