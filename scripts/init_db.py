"""
Initialize the database — create all tables.

Run this from the project root:
    python scripts/init_db.py

Safe to run multiple times — only creates tables that don't exist.

This is the same operation the FastAPI lifespan performs on startup,
exposed as a standalone CLI for fresh installs, debugging, and as a
way to verify your DATABASE_URL is configured correctly without
needing to launch uvicorn.
"""

"""
Initialize the database — create all tables.

For DEVELOPMENT: python scripts/init_db.py
  Creates tables directly via SQLAlchemy create_all().
  Fast, no migration history.

For PRODUCTION: use Alembic instead:
  alembic upgrade head
  This tracks schema history and supports incremental changes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.infra.database import init_db  # noqa: E402


async def main() -> None:
    print(f"Initializing database: {settings.database_url}")
    try:
        await init_db()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print("✓ Tables created (or already existed).")
    print("  Note: For production schema changes, use: alembic upgrade head")


if __name__ == "__main__":
    asyncio.run(main())