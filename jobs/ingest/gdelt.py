"""
Daily conflict events from The GDELT Project's daily events file (GDELT 1.0, free).

One request per day: https://data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip (about
6 MB zipped, ~100k coded events). From it we keep:
  - root events with a conflict-type CAMEO code (protest, force posture, coercion, assault,
    fight, mass violence), placed at a state, city or landmark (not a whole country);
  - at least MIN_ARTICLES articles, one event per source URL (the best-covered one);
  - at most PER_COUNTRY per country, so one country's coverage cannot fill the globe.
Each kept event's headline and description come from the article page's own og:title /
og:description tags; events whose page gives neither are dropped. Rows come out in the same
shape as data/backup_csvs/finalconflictCSV.csv, so jobs.ingest.run.load_conflict_csv loads them.

Run:  python -m jobs.ingest.gdelt [--day YYYYMMDD] [--limit 150]   (prints rows; writes nothing)
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from jobs.ingest.images import LOOKUP_ERRORS, MAX_PAGE_BYTES, TIMEOUT, _request  # noqa: E402

DAILY_URL = "https://data.gdeltproject.org/events/{day}.export.CSV.zip"
MIN_ARTICLES = 10
PER_COUNTRY = 15
LIMIT = 150
PAGE_WORKERS = 16

# GDELT 1.0 daily export columns (58, tab-separated). See the GDELT Event Codebook.
SQLDATE, ACTOR1_NAME, ACTOR2_NAME, IS_ROOT = 1, 6, 16, 25
EVENT_CODE, ROOT_CODE, GOLDSTEIN, NUM_ARTICLES, AVG_TONE = 26, 28, 30, 33, 34
GEO_TYPE, GEO_NAME, GEO_COUNTRY, GEO_LAT, GEO_LNG = 49, 50, 51, 53, 54
SOURCE_URL = 57
COLUMN_COUNT = 58
PLACED_GEO_TYPES = {"2", "3", "4", "5"}  # US state, US city, world city, world state; 1 is a whole country

META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SITE_SUFFIX = re.compile(r"\s+[|\-–—]\s+[^|\-–—]{2,40}$")  # " | Site name" or " - Site" after a <title>
PIPE_SUFFIX = re.compile(r"\s+\|\s+[^|]{2,40}$")  # og:title is cleaner; only a " | Site" tail is trimmed


def category_for(event_code: str) -> str | None:
    """Map a CAMEO event code to one of the conflict layers, or None to skip it."""
    root = event_code[:2]
    if root == "14":
        return "protest"
    if event_code.startswith("183"):  # suicide, car, roadside or location bombing
        return "terror"
    if root == "18" or event_code.startswith("173"):  # assault, abduction, killing; arrest
        return "crime"
    if root in ("15", "19", "20") or event_code.startswith("175"):  # force posture, fighting, repression
        return "conflict"
    if root == "17":  # other coercion: sanctions, expulsions, seizures
        return "politics"
    return None


def candidates(rows: list[list[str]], since: str, *, min_articles: int = MIN_ARTICLES,
               per_country: int = PER_COUNTRY, limit: int) -> list[list[str]]:
    """Best-covered conflict events, one per URL, spread across countries. Pure."""
    best: dict[str, list[str]] = {}
    for row in rows:
        if len(row) != COLUMN_COUNT or row[IS_ROOT] != "1" or row[SQLDATE] < since:
            continue
        if category_for(row[EVENT_CODE]) is None or row[GEO_TYPE] not in PLACED_GEO_TYPES:
            continue
        if not row[GEO_LAT] or not row[GEO_LNG] or not row[SOURCE_URL].startswith("http"):
            continue
        if not row[NUM_ARTICLES].isdigit() or int(row[NUM_ARTICLES]) < min_articles:
            continue
        current = best.get(row[SOURCE_URL])
        if current is None or int(row[NUM_ARTICLES]) > int(current[NUM_ARTICLES]):
            best[row[SOURCE_URL]] = row
    ranked = sorted(best.values(), key=lambda row: (-int(row[NUM_ARTICLES]), row[SOURCE_URL]))
    taken: dict[str, int] = {}
    picked = []
    for row in ranked:
        country = row[GEO_COUNTRY] or "?"
        if taken.get(country, 0) >= per_country:
            continue
        taken[country] = taken.get(country, 0) + 1
        picked.append(row)
        if len(picked) == limit:
            break
    return picked


def _meta(head: str, *names: str) -> str | None:
    for tag in META_TAG.findall(head):
        key = re.search(r"\b(?:property|name)\s*=\s*[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        value = re.search(r"\bcontent\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
        if key and value and key.group(1).lower() in names and value.group(1).strip():
            return html.unescape(value.group(1)).strip()
    return None


def page_text(head: str) -> dict:
    """Headline and description from a page's <head>. Pure."""
    title = _meta(head, "og:title", "twitter:title")
    if title:
        title = PIPE_SUFFIX.sub("", title)
    else:
        match = TITLE_TAG.search(head)
        title = SITE_SUFFIX.sub("", html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()) if match else None
    description = _meta(head, "og:description", "twitter:description", "description")
    return {"title": title or None, "description": description}


def fetch_page_text(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(_request(url, accept="text/html"), timeout=TIMEOUT) as response:
            head = response.read(MAX_PAGE_BYTES).decode(response.headers.get_content_charset() or "utf-8", "replace")
    except LOOKUP_ERRORS:
        return None
    except Exception:  # noqa: BLE001 — one unreachable article never stops the day
        return None
    text = page_text(head.split("</head>", 1)[0])
    return text if text["title"] else None


def fetch_day(day: str) -> list[list[str]]:
    request = urllib.request.Request(DAILY_URL.format(day=day), headers={"User-Agent": "eye-of-horus/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    with archive.open(archive.namelist()[0]) as handle:
        text = io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
        return list(csv.reader(text, delimiter="\t"))


def to_conflict_row(row: list[str], text: dict) -> dict:
    """The finalconflictCSV.csv shape that load_conflict_csv reads."""
    return {
        "SQLDATE": row[SQLDATE], "Actor1Name": row[ACTOR1_NAME], "Actor2Name": row[ACTOR2_NAME],
        "EventCode": row[EVENT_CODE], "ActionGeo_FullName": row[GEO_NAME],
        "ActionGeo_Lat": row[GEO_LAT], "ActionGeo_Long": row[GEO_LNG], "GoldsteinScale": row[GOLDSTEIN],
        "AvgTone": row[AVG_TONE], "NumArticles": row[NUM_ARTICLES], "SOURCEURL": row[SOURCE_URL],
        "title": text["title"], "description": text["description"] or "", "category": category_for(row[EVENT_CODE]),
    }


def daily_rows(day: str | None = None, limit: int = LIMIT, fetch_text=fetch_page_text) -> tuple[str, list[dict]]:
    """(day, rows) for one day's conflict events; `day` defaults to yesterday (UTC)."""
    day = day or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    since = (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    # Twice the limit: some article pages block robots or have no headline.
    picked = candidates(fetch_day(day), since, limit=limit * 2)
    with futures.ThreadPoolExecutor(PAGE_WORKERS) as pool:
        texts = list(pool.map(fetch_text, [row[SOURCE_URL] for row in picked]))
    rows = [to_conflict_row(row, text) for row, text in zip(picked, texts) if text]
    return day, rows[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview one day's GDELT conflict events (writes nothing)")
    parser.add_argument("--day", help="YYYYMMDD (default: yesterday, UTC)")
    parser.add_argument("--limit", type=int, default=LIMIT)
    args = parser.parse_args()
    day, rows = daily_rows(args.day, args.limit)
    print(json.dumps({"day": day, "events": len(rows), "sample": rows[:3]}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
