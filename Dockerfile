FROM python:3.12-slim AS build

ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

RUN pip install --no-cache-dir poetry==2.5.1

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root


FROM python:3.12-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 1000 gateway

WORKDIR /app
COPY --from=build /app/.venv .venv
COPY src src

USER gateway
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"

# One worker: payments and idempotency keys live in memory, so a second process would not see them.
CMD ["uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
