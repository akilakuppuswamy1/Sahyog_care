"""Park expired W3 reviews; schedule with the deployment's periodic job runner."""
from __future__ import annotations

import json
import os
from pathlib import Path

from services.agent.w3.nodes import park_expired_reviews
from services.agent.w3.store import PostgresW3Store

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as conn:
        parked = park_expired_reviews(PostgresW3Store(conn))
    print(json.dumps({"parked_draft_ids": parked}))


if __name__ == "__main__":
    main()
