"""
Mint a JWT from the command line.

Usage:
    python scripts/generate_token.py <user_id> [tier]

Examples:
    python scripts/generate_token.py alice
    python scripts/generate_token.py alice pro
    python scripts/generate_token.py admin-test enterprise

The output is just the token string — pipe or copy as needed:

    $env:TOKEN = python scripts/generate_token.py alice    # PowerShell
    TOKEN=$(python scripts/generate_token.py alice)        # bash

The token is signed with the same JWT_SECRET as the running gateway,
so it works immediately against a local server.
"""

import sys
from pathlib import Path

# Make the project root importable when this script is run directly.
# See the same note in scripts/init_db.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
from typing import Literal, get_args  # noqa: E402

from app.core.security import create_access_token  # noqa: E402

Tier = Literal["free", "pro", "enterprise"]
VALID_TIERS = list(get_args(Tier))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mint a JWT for the rate-limited-gateway.",
    )
    parser.add_argument(
        "user_id",
        help="User ID to embed in the token (any string).",
    )
    parser.add_argument(
        "tier",
        nargs="?",
        default="free",
        choices=VALID_TIERS,
        help=f"Tier to embed. One of {VALID_TIERS}. Defaults to 'free'.",
    )
    args = parser.parse_args()

    try:
        token = create_access_token(user_id=args.user_id, tier=args.tier)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(token)


if __name__ == "__main__":
    main()