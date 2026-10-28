FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# pip ставится **из wheel с проверкой SHA-256**, и только потом — poetry.
#
# Причина: у любого pip ниже 26.2.2 встроенный certifi грузится **вдобавок**
# к системному хранилищу и отключить это нельзя, поэтому такой pip доверяет
# подделанным сертификатам eMudhra (инцидент 22.10.2026, все восемь CA на
# ключах с d < 256).
#
# `pip install --upgrade pip poetry` — это ровно та ловушка, о которой
# предупреждает объявление: **старый pip скачивает новый через
# скомпрометированное доверие.** Сейчас в `python:3.14-slim` уже 26.2.2, но
# тег плавающий — образ мог быть собран когда угодно и с чем угодно.
#
# Хеш делает установку безопасной независимо от того, какой pip её
# запускает: подменённый файл не сойдётся с SHA-256, и сборка остановится.
RUN pip install --no-cache-dir \
        "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
    && pip --version

# Poetry **закреплён**: без версии сборка невоспроизводима, и «вчера
# работало» ничего не значит. `--upgrade pip` убран — pip уже обновлён
# строкой выше из wheel, а не через себя же.
RUN pip install --no-cache-dir "poetry==2.4.1"

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY requirements.production.txt ./
RUN pip install --requirement requirements.production.txt

COPY . .

RUN chmod +x /app/deployment/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/deployment/docker/entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
