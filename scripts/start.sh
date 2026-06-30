#!/bin/sh
# Entrypoint for production container.
# Runs Alembic migrations first so every deploy is schema-current,
# then starts gunicorn with uvicorn workers.
set -e

echo "Running database migrations..."
python -m alembic upgrade head

echo "Starting gateway..."
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 2 \
  -b 0.0.0.0:8000 \
  --timeout 60 \
  --graceful-timeout 30 \
  --access-logfile -
