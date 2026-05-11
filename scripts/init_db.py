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

import sys
from pathlib import Path

# Make the project root importable when this script is run directly:
#   python scripts/init_db.py
# Without this, Python only adds scripts/ to sys.path and `app` is
# not findable. This shim makes the script work whether or not the
# project has been installed via `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio  # noqa: E402  (imports after sys.path edit, intentional)

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


if __name__ == "__main__":
    asyncio.run(main())