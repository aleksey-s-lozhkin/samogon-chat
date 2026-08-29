FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

WORKDIR /app

RUN pip install --upgrade pip poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root
COPY requirements.production.txt ./
RUN pip install --requirement requirements.production.txt

COPY . .

RUN chmod +x /app/deployment/docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/deployment/docker/entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
