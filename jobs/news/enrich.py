"""
Enrich pending news items (raw.feed_item) and add them to mart.event.

Only rows with status 'pending' are read, so nothing is ever enriched twice: each item ends
'ingested' (added to mart.event) or 'rejected' (with the reason in `verdict`) as soon as its
batch returns. Items are sent to Gemini in batches (BATCH per call) to stay well inside the
free tier's daily request allowance.

Per item, Gemini returns an English summary, a category, where the story happens, keywords,
and significance. The place is geocoded with OpenStreetMap; when that fails the story sits
at the centre of the country it is about. Stories with no single place (a worldwide market
move, a global summit with no host named) cannot go on the globe and are rejected.

Run:  python -m jobs.news.enrich [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
for path in (API_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from db import LAYER_IDS, clean_terms, database_configured, database_url, load_repo_env  # noqa: E402
from jobs.news import geocode, sources  # noqa: E402

BATCH = 12  # items per Gemini call
MODEL = "gemini-3.5-flash-lite"
SPECIFICITY = {"precise": "point", "city": "city", "region": "region", "country": "country"}
COUNTRIES_GEOJSON = REPO_ROOT / "apps" / "web" / "src" / "data" / "countries.geojson.json"

PROMPT = """\
You enrich world news items from international outlets (BBC News, The Guardian, Al Jazeera).

For EACH item return an object with exactly these fields:
- "id": the item's id, unchanged
- "aboutCountryIso3": ISO 3166-1 alpha-3 code of the ONE country the story mainly happens in
  or concerns. Use "" only for genuinely international stories with no single main country
  (e.g. a worldwide market move).
- "summary": 2-3 sentence English summary for a general reader, with context and key details
- "layerId": the best category, one of: {layers}
- "location": {{"country": full country name or "", "region": state/province or "", "city": city/town or ""}}
  for where the story happens
- "locationSpecificity": "global" | "continent" | "country" | "region" | "city" | "precise"
- "keywords": 6-10 objects {{"text": ..., "relevance": 0.6-1.0, "type": "topic|entity|concept|location|technology|event|industry|organization|person"}}
- "significance": integer 0-100 newsworthiness (0-20 niche/local, 21-40 regional, 41-60 national,
  61-80 international, 81-100 major global event)

Respond with a JSON array only, one object per item, in any order.

ITEMS:
{items}
"""


def build_prompt(batch: list[dict]) -> str:
    items = [{
        "id": item["item_id"],
        "outlet": item["publisher_name"],
        "title": item["title"],
        "summary": (item["summary"] or "")[:500],
    } for item in batch]
    return PROMPT.format(layers=", ".join(LAYER_IDS), items=json.dumps(items, ensure_ascii=False, indent=1))


def placement(result: dict, coords: dict | None, centroids: dict[str, tuple[float, float]]) -> dict | None:
    """Where the story goes on the globe, or None when it has no single place."""
    specificity = result.get("locationSpecificity") or "country"
    about = str(result.get("aboutCountryIso3") or "").strip().upper() or None
    if coords and specificity in SPECIFICITY:
        return {"lat": coords["lat"], "lng": coords["lng"], "geo_precision": SPECIFICITY[specificity],
                "geo_source": "openstreetmap", "country_iso3": about}
    if about in centroids:
        lat, lng = centroids[about]
        return {"lat": lat, "lng": lng, "geo_precision": "country", "geo_source": "centroid", "country_iso3": about}
    return None


def verdict_for(result: dict, placed: dict | None) -> dict:
    about = str(result.get("aboutCountryIso3") or "").strip().upper() or None
    return {
        "aboutCountryIso3": about, "placed": placed is not None,
        "reason": None if placed else "no single place to show on the globe",
        "model": MODEL, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def country_centroids() -> dict[str, tuple[float, float]]:
    """Fallback point per ISO3: the centre of the country's largest polygon's bounding box."""
    data = json.loads(COUNTRIES_GEOJSON.read_text(encoding="utf-8"))
    centroids = {}
    for feature in data["features"]:
        geometry = feature["geometry"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        ring = max((polygon[0] for polygon in polygons), key=len)
        lngs, lats = [p[0] for p in ring], [p[1] for p in ring]
        centroids[feature["id"]] = ((min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2)
    return centroids


def call_gemini(client, prompt: str) -> list[dict]:
    response = client.models.generate_content(model=MODEL, contents=prompt, config={"response_mime_type": "application/json"})
    data = json.loads(response.text.strip())
    return data if isinstance(data, list) else []


_EVENT_INSERT = """
INSERT INTO mart.event (
  event_id, source, source_event_id, category, title, summary, info_url, image_url,
  occurred_at, updated_at, lng, lat, geo_precision, geo_source, significance,
  country_iso3, keywords, publisher_id, language, verification
) VALUES (
  %(event_id)s, %(source)s, %(item_id)s, %(category)s, %(title)s, %(summary)s, %(url)s, %(image_url)s,
  %(occurred_at)s, %(occurred_at)s, %(lng)s, %(lat)s, %(geo_precision)s, %(geo_source)s, %(significance)s,
  %(country_iso3)s, %(keywords)s::jsonb, %(publisher_id)s, 'en', %(verification)s::jsonb
)
ON CONFLICT DO NOTHING
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich pending news items and add them to mart.event")
    parser.add_argument("--limit", type=int, default=0, help="Only process this many pending items")
    args = parser.parse_args()

    load_repo_env()
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not database_configured() or not key:
        raise SystemExit("DATABASE_URL and GOOGLE_API_KEY are required")
    import psycopg
    from google import genai  # type: ignore

    client = genai.Client(api_key=key)
    centroids = country_centroids()
    counts = {"ingested": 0, "rejected": 0, "unanswered": 0, "geocoded": 0, "centroid": 0}
    with psycopg.connect(database_url(), connect_timeout=20) as conn:
        rows = conn.execute("""
            SELECT f.item_id, f.publisher_id, p.name, f.url, f.title, f.summary, f.image_url, f.published_at, f.fetched_at
            FROM raw.feed_item f JOIN mart.publisher p USING (publisher_id)
            WHERE f.status = 'pending' AND f.publisher_id = ANY(%s)
            ORDER BY f.published_at DESC NULLS LAST
        """, (list(sources.SOURCE_BY_PUBLISHER),)).fetchall()
        keys = ("item_id", "publisher_id", "publisher_name", "url", "title", "summary", "image_url", "published_at", "fetched_at")
        pending = [dict(zip(keys, row)) for row in rows][: args.limit or None]
        print(f"{len(pending)} pending items → {-(-len(pending) // BATCH)} Gemini calls")

        for start in range(0, len(pending), BATCH):
            batch = pending[start:start + BATCH]
            try:
                results = call_gemini(client, build_prompt(batch))
            except Exception as error:  # noqa: BLE001 — quota or network: stop; the rest stays pending
                print(f"  batch {start // BATCH + 1}: Gemini failed ({str(error)[:120]}); stopping, {len(pending) - start} items left pending")
                break
            by_id = {str(result.get("id")): result for result in results if isinstance(result, dict)}
            for item in batch:
                result = by_id.get(item["item_id"])
                if result is None:
                    counts["unanswered"] += 1  # left pending: retried on the next run
                    continue
                specificity = result.get("locationSpecificity") or "country"
                query = geocode.build_query(result.get("location") or {}, specificity)
                coords = None
                if query and specificity in SPECIFICITY:
                    coords = geocode.geocode(query)
                    time.sleep(1.1)  # Nominatim fair use
                placed = placement(result, coords, centroids)
                verdict = verdict_for(result, placed)
                if placed is None:
                    conn.execute("UPDATE raw.feed_item SET status = 'rejected', verdict = %s::jsonb WHERE item_id = %s",
                                 (json.dumps(verdict), item["item_id"]))
                    counts["rejected"] += 1
                    continue
                counts["geocoded" if placed["geo_source"] == "openstreetmap" else "centroid"] += 1
                significance = result.get("significance")
                layer = result.get("layerId") if result.get("layerId") in LAYER_IDS else "news"
                conn.execute(_EVENT_INSERT, {
                    "event_id": f"rss:{item['item_id'][:20]}", "source": sources.SOURCE_BY_PUBLISHER[item["publisher_id"]],
                    "item_id": item["item_id"], "category": layer, "title": item["title"],
                    "summary": result.get("summary") or item["summary"], "url": item["url"], "image_url": item["image_url"],
                    "occurred_at": item["published_at"] or item["fetched_at"], **placed,
                    "significance": float(significance) if isinstance(significance, (int, float)) and 0 <= significance <= 100 else 30.0,
                    "keywords": json.dumps(clean_terms(result.get("keywords"))),
                    "publisher_id": item["publisher_id"], "verification": json.dumps(verdict),
                })
                conn.execute("UPDATE raw.feed_item SET status = 'ingested', verdict = %s::jsonb WHERE item_id = %s",
                             (json.dumps({**verdict, "summary": result.get("summary"), "layerId": layer}), item["item_id"]))
                counts["ingested"] += 1
            conn.commit()  # per batch: a crash never repeats finished items
            print(f"  batch {start // BATCH + 1}/{-(-len(pending) // BATCH)}: {counts}")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
