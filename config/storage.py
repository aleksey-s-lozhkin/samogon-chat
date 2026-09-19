from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class SamogonManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Разрешает Jazzmin использовать каталог тем как data-атрибут.

    Jazzmin 3.0.5 передаёт ``vendor/bootswatch`` в ``{% static %}`` для
    клиентского переключателя тем. Это каталог, а не файл, поэтому строгий
    manifest Django не создаёт для него запись и рендер админки падает.
    Сам переключатель отключён, но атрибут всё равно присутствует в шаблоне.
    """

    _JAZZMIN_THEME_DIRECTORY = "vendor/bootswatch"

    def stored_name(self, name):
        if name.rstrip("/") == self._JAZZMIN_THEME_DIRECTORY:
            return self._JAZZMIN_THEME_DIRECTORY
        return super().stored_name(name)
