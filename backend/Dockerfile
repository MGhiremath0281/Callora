FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/

EXPOSE 8000

# Apply pending migrations, then serve. Migrations are idempotent so
# restarting the container is safe.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
