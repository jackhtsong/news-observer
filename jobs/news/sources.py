"""
The news outlets Eye of Horus reads: three world-news desks with official, free RSS feeds.

`sync` writes them to mart.publisher and mart.feed (idempotent) and pauses any other
publisher, so jobs.ingest.rss only fetches these feeds. Each outlet's `source` is the
mart.event.source value its stories get, and the label the globe shows on cards.

Run:  python -m jobs.news.sources
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from db import database_configured, database_url, load_repo_env  # noqa: E402

# country_iso3 is where the outlet is based; stories are placed where they happen.
OUTLETS = (
    {"source": "bbc", "name": "BBC News", "domain": "bbc.co.uk", "country_iso3": "GBR",
     "feed_url": "https://feeds.bbci.co.uk/news/world/rss.xml", "title": "BBC News - World"},
    {"source": "guardian", "name": "The Guardian", "domain": "theguardian.com", "country_iso3": "GBR",
     "feed_url": "https://www.theguardian.com/world/rss", "title": "The Guardian - World news"},
    {"source": "aljazeera", "name": "Al Jazeera", "domain": "aljazeera.com", "country_iso3": "QAT",
     "feed_url": "https://www.aljazeera.com/xml/rss/all.xml", "title": "Al Jazeera - All news"},
)
NEWS_SOURCES = tuple(outlet["source"] for outlet in OUTLETS)


def publisher_id(outlet: dict) -> str:
    return f"pub:{outlet['domain']}"


SOURCE_BY_PUBLISHER = {publisher_id(outlet): outlet["source"] for outlet in OUTLETS}


def sync(conn) -> None:
    """Register the outlets and their feeds; pause every other publisher."""
    ids = [publisher_id(outlet) for outlet in OUTLETS]
    for outlet in OUTLETS:
        conn.execute("""
            INSERT INTO mart.publisher (publisher_id, name, domain, country_iso3, languages, scope, directory, status)
            VALUES (%s, %s, %s, %s, '{en}', 'international', 'manual', 'approved')
            ON CONFLICT (publisher_id) DO UPDATE
              SET name = EXCLUDED.name, country_iso3 = EXCLUDED.country_iso3, scope = EXCLUDED.scope, status = 'approved'
        """, (publisher_id(outlet), outlet["name"], outlet["domain"], outlet["country_iso3"]))
        conn.execute("""
            INSERT INTO mart.feed (feed_url, publisher_id, title)
            VALUES (%s, %s, %s)
            ON CONFLICT (feed_url) DO UPDATE SET publisher_id = EXCLUDED.publisher_id, title = EXCLUDED.title
        """, (outlet["feed_url"], publisher_id(outlet), outlet["title"]))
    conn.execute("UPDATE mart.publisher SET status = 'paused' WHERE NOT (publisher_id = ANY(%s)) AND status = 'approved'", (ids,))


def main() -> None:
    load_repo_env()
    if not database_configured():
        raise SystemExit("DATABASE_URL is not set")
    import psycopg

    with psycopg.connect(database_url(), connect_timeout=20) as conn:
        sync(conn)
        conn.commit()
    print(f"Registered {len(OUTLETS)} outlets: {', '.join(outlet['name'] for outlet in OUTLETS)}")


if __name__ == "__main__":
    main()
