FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml .
RUN mkdir -p app && pip install --no-cache-dir ".[prod]"

COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .
COPY scripts/start.sh .

RUN chmod +x start.sh \
    && useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

ENTRYPOINT ["./start.sh"]
