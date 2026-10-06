"""Create or update the database schema: apply the migrations in MIGRATIONS, in order.

Every migration is safe to re-run. sql/009_drop_hazards.sql is not in the list: it deletes
data, so run it by hand (psql "$DATABASE_URL" -f sql/009_drop_hazards.sql) only on an older
database that still holds the retired natural-hazard layers.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from db import database_configured, database_url, load_repo_env  # noqa: E402

MIGRATIONS = (
    "001_init.sql",
    "002_links_update.sql",
    "003_acled.sql",
    "005_keywords_migration.sql",
    "006_event_image.sql",
    "007_local_news.sql",
    "008_event_publisher.sql",
    "010_link_progress.sql",
)


def main() -> None:
    load_repo_env()
    if not database_configured():
        print(
            "DATABASE_URL is empty. Paste the Postgres (Neon) connection string into .env "
            "(see .env.example), then run: python -m jobs.ingest.apply_schema"
        )
        raise SystemExit(1)
    import psycopg

    with psycopg.connect(database_url(), connect_timeout=30) as conn:
        with conn.cursor() as cur:
            for name in MIGRATIONS:
                # One call per file: without parameters, psycopg sends all of its statements,
                # including DO $$ … $$ blocks that a split on ";" would break.
                cur.execute((REPO_ROOT / "sql" / name).read_text(encoding="utf-8"))
                print(f"Applied sql/{name}")
        conn.commit()


if __name__ == "__main__":
    main()
