FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
# Copy minimal stub so pip can resolve the package metadata without full source
RUN mkdir -p app && pip install --user --no-cache-dir ".[prod]"

FROM python:3.12-slim
WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

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
