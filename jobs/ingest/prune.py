"""
Keep the database small: remove events, links, and raw rows older than --days (default 90).

Neon's free plan holds 0.5 GB; news plus GDELT adds well under 1 MB a day, so a 90-day window
stays far below it. Detail rows and tags go with their events (ON DELETE CASCADE). Run daily
after ingest; --dry-run only counts.

Run:  python -m jobs.ingest.prune [--days 90] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from db import database_configured, database_url, load_repo_env  # noqa: E402

# (label, count query, delete query); each takes the cutoff in days as %(days)s.
STEPS = (
    ("links", """
        SELECT count(*) FROM mart.event_link l
        WHERE EXISTS (SELECT 1 FROM mart.event e WHERE e.event_id IN (l.source_id, l.target_id)
                      AND e.occurred_at < now() - make_interval(days => %(days)s))
    """, """
        DELETE FROM mart.event_link l
        USING mart.event e
        WHERE e.event_id IN (l.source_id, l.target_id) AND e.occurred_at < now() - make_interval(days => %(days)s)
    """),
    ("events", """
        SELECT count(*) FROM mart.event WHERE occurred_at < now() - make_interval(days => %(days)s)
    """, """
        DELETE FROM mart.event WHERE occurred_at < now() - make_interval(days => %(days)s)
    """),
    ("feed_items", """
        SELECT count(*) FROM raw.feed_item WHERE fetched_at < now() - make_interval(days => %(days)s)
    """, """
        DELETE FROM raw.feed_item WHERE fetched_at < now() - make_interval(days => %(days)s)
    """),
    ("ingest_batches", """
        SELECT count(*) FROM raw.ingest_batch WHERE pulled_at < now() - make_interval(days => %(days)s)
    """, """
        DELETE FROM raw.ingest_batch WHERE pulled_at < now() - make_interval(days => %(days)s)
    """),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove data older than --days")
    parser.add_argument("--days", type=int, default=90, help="Keep this many days (minimum 7)")
    parser.add_argument("--dry-run", action="store_true", help="Count what would be removed; change nothing")
    args = parser.parse_args()
    if args.days < 7:
        raise SystemExit("--days must be at least 7")

    load_repo_env()
    if not database_configured():
        raise SystemExit("DATABASE_URL is not set")
    import psycopg

    counts = {}
    with psycopg.connect(database_url(), connect_timeout=20) as conn:
        for label, count_sql, delete_sql in STEPS:
            if args.dry_run:
                counts[label] = conn.execute(count_sql, {"days": args.days}).fetchone()[0]
            else:
                counts[label] = conn.execute(delete_sql, {"days": args.days}).rowcount
        if not args.dry_run:
            conn.commit()
    print(json.dumps({"days": args.days, "dryRun": args.dry_run, "removed" if not args.dry_run else "wouldRemove": counts}))


if __name__ == "__main__":
    main()
