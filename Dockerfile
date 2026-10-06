FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SERVICE_ROLE=api

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "if [ \"${SERVICE_ROLE}\" = worker ]; then exec python -m app.cli worker; fi; alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
