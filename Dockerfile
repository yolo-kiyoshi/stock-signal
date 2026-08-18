FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

RUN pip install --upgrade pip && pip install -e ".[dev]"

RUN mkdir -p /app/data /app/models /app/reports

CMD ["python", "-m", "stock_signal", "health"]
