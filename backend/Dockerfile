FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
RUN mkdir -p /data && chown -R nobody:nogroup /data /app

USER nobody

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.migrations.sqlite_to_postgres && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]

