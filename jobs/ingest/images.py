"""
Find a thumbnail for events that have none, and store it in mart.event.image_url.

Every source uses the linked article page's og:image (or twitter:image) meta tag. Each
candidate is requested the way the browser will load it (no Referer, https) and kept only if
it answers with an image, so the cards never point at a broken thumbnail.

Used by jobs.ingest.backfill_images (daily, after enrichment) and at the end of run_ingest().
BBC and Guardian items usually arrive with their feed's image already; Al Jazeera's feed has
none, and GDELT rows only carry a URL.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jobs.news.sources import NEWS_SOURCES  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; eye-of-horus/1.0; +https://github.com/tm1944/eye-of-horus)"
TIMEOUT = 12
MAX_PAGE_BYTES = 1_000_000  # og:image lives in <head>; never read whole pages
SOURCES = (*NEWS_SOURCES, "conflict_csv")
META_IMAGE = re.compile(
    r"<meta\b[^>]*?(?:property|name)\s*=\s*[\"'](?:og:image(?::secure_url)?|og:image:url|twitter:image(?::src)?)[\"'][^>]*>",
    re.IGNORECASE,
)
CONTENT = re.compile(r"\bcontent\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
LOOKUP_ERRORS = (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, IndexError)


def _request(url: str, *, accept: str) -> urllib.request.Request:
    # No Referer, matching the browser's referrerPolicy="no-referrer".
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})


def page_image(page_url: str) -> str | None:
    with urllib.request.urlopen(_request(page_url, accept="text/html"), timeout=TIMEOUT) as response:
        head = response.read(MAX_PAGE_BYTES).decode(response.headers.get_content_charset() or "utf-8", "replace")
    for tag in META_IMAGE.findall(head):
        content = CONTENT.search(tag)
        if content:
            return urllib.parse.urljoin(page_url, html.unescape(content.group(1).strip()))
    return None


def candidate_image(source: str, source_event_id: str, info_url: str | None) -> str | None:
    if not info_url:
        return None
    return page_image(info_url)


def usable_image(url: str) -> str | None:
    """The URL (upgraded to https when that works) if it serves an image without a Referer."""
    candidates = [url]
    if url.startswith("http://"):
        candidates.insert(0, "https://" + url[len("http://"):])  # https pages cannot show http images
    for candidate in candidates:
        if not candidate.startswith("https://"):
            continue
        try:
            with urllib.request.urlopen(_request(candidate, accept="image/*"), timeout=TIMEOUT) as response:
                if response.headers.get_content_type().startswith("image/"):
                    return candidate
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
    return None


def fill_missing_images(conn, *, sources=SOURCES, limit: int = 0, dry_run: bool = False,
                        log: Callable[[str], None] = print) -> dict:
    """Look up and store images for events that have none. Commits per row, so progress survives."""
    rows = conn.execute("""
        SELECT event_id, source, source_event_id, info_url FROM mart.event
        WHERE source = ANY(%s) AND image_url IS NULL
        ORDER BY source, significance DESC, event_id
    """, (list(sources),)).fetchall()
    if limit:
        rows = rows[:limit]
    outcomes = ("usable", "no_image", "page_error", "image_broken")
    counts: dict[str, dict[str, int]] = {}
    for event_id, source, source_event_id, info_url in rows:
        tally = counts.setdefault(source, {"events": 0, **{name: 0 for name in outcomes}})
        tally["events"] += 1
        try:
            candidate = candidate_image(source, source_event_id, info_url)
        except LOOKUP_ERRORS as exc:
            tally["page_error"] += 1
            log(f"  page error   {event_id}  {type(exc).__name__}: {str(exc)[:80]}")
            continue
        if not candidate:
            tally["no_image"] += 1
            log(f"  no image     {event_id}  {(info_url or '')[:70]}")
            continue
        image = usable_image(candidate)
        if not image:
            tally["image_broken"] += 1
            log(f"  broken image {event_id}  {candidate[:90]}")
            continue
        tally["usable"] += 1
        log(f"  ok           {event_id}  {image[:90]}")
        if not dry_run:
            conn.execute("UPDATE mart.event SET image_url = %s WHERE event_id = %s AND image_url IS NULL", (image, event_id))
            conn.commit()
    return {"sources": counts, "dry_run": dry_run}
