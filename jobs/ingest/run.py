"""Load conflict events (ACLED when configured, and the GDELT conflict CSV) into Postgres.

ACLED runs only when its credentials are set in .env. Apply the schema first:
python -m jobs.ingest.apply_schema
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from db import database_configured, database_url, load_repo_env  # noqa: E402

ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
ACLED_READ_URL = "https://acleddata.com/api/acled/read"
ACLED_INFO_URL = "https://acleddata.com/"
ACLED_CAP = 5000
ACLED_PAGE_SIZE = 5000
ACLED_CONFLICT_TYPES = {
    "Battles",
    "Explosions/Remote violence",
    "Violence against civilians",
    "Riots",
}
_ACLED_KIND_TABLE = {
    "conflict": "mart.conflict",
    "protest": "mart.protest",
    "strategic_development": "mart.strategic_development",
}
_ACLED_KIND_FIELDS = (
    "event_type",
    "disorder_type",
    "fatalities",
    "civilian_targeting",
    "actor1",
    "assoc_actor_1",
    "actor2",
    "assoc_actor_2",
    "inter1",
    "inter2",
    "interaction",
    "time_precision",
    "geo_precision_code",
    "location",
    "admin1",
    "admin2",
    "admin3",
    "region",
    "country",
    "iso",
    "source_name",
    "source_scale",
    "population_best",
)

_EVENT_UPSERT = """
INSERT INTO mart.event (
  event_id, source, source_event_id, category, subtype, title, summary, info_url,
  occurred_at, updated_at, ended_at, lng, lat, alt_m, geo_precision, geo_source,
  significance, weight, country_iso3, entities, raw_ref
) VALUES (
  %(event_id)s, %(source)s, %(source_event_id)s, %(category)s, %(subtype)s,
  %(title)s, %(summary)s, %(info_url)s, %(occurred_at)s, %(updated_at)s,
  %(ended_at)s, %(lng)s, %(lat)s, %(alt_m)s, %(geo_precision)s, %(geo_source)s,
  %(significance)s, %(weight)s, %(country_iso3)s, %(entities)s::jsonb, %(raw_ref)s
)
ON CONFLICT (source, source_event_id) DO UPDATE SET
  category = EXCLUDED.category,
  subtype = EXCLUDED.subtype,
  title = EXCLUDED.title,
  summary = EXCLUDED.summary,
  info_url = EXCLUDED.info_url,
  occurred_at = EXCLUDED.occurred_at,
  updated_at = EXCLUDED.updated_at,
  ended_at = EXCLUDED.ended_at,
  lng = EXCLUDED.lng,
  lat = EXCLUDED.lat,
  alt_m = EXCLUDED.alt_m,
  geo_precision = EXCLUDED.geo_precision,
  geo_source = EXCLUDED.geo_source,
  significance = EXCLUDED.significance,
  weight = EXCLUDED.weight,
  country_iso3 = EXCLUDED.country_iso3,
  entities = EXCLUDED.entities,
  raw_ref = EXCLUDED.raw_ref,
  ingested_at = now()
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect():
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=20)


def _record_batch(cur, source: str, payload) -> str:
    batch_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO raw.ingest_batch (batch_id, source, pulled_at, payload)
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (batch_id, source, _now(), json.dumps(payload)),
    )
    return batch_id


def _blank_event(**overrides) -> dict:
    row = {
        "event_id": None,
        "source": None,
        "source_event_id": None,
        "category": None,
        "subtype": None,
        "title": None,
        "summary": None,
        "info_url": None,
        "occurred_at": None,
        "updated_at": None,
        "ended_at": None,
        "lng": None,
        "lat": None,
        "alt_m": None,
        "geo_precision": "point",
        "geo_source": "native",
        "significance": 0,
        "weight": None,
        "country_iso3": None,
        "entities": "[]",
        "raw_ref": None,
    }
    row.update(overrides)
    return row


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    numeric = _num(value)
    if numeric is None:
        return None
    return int(numeric)


def _iso3(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("iso3")
    if not value:
        return None
    text = str(value).strip().upper()
    return text[:3] if text else None


def acled_category(event_type: str | None) -> str | None:
    if event_type in ACLED_CONFLICT_TYPES:
        return "conflict"
    if event_type == "Protests":
        return "protest"
    if event_type == "Strategic developments":
        return "strategic_development"
    return None


def acled_geo_precision(code) -> str:
    return {1: "point", 2: "city", 3: "region"}.get(_int(code) or 0, "point")


def acled_significance(fatalities: int | None, civilian_targeting: str | None) -> float:
    score = 55.0
    if fatalities:
        score += min(30.0, fatalities * 5)
    if civilian_targeting and str(civilian_targeting).strip():
        score += 15
    return min(100.0, score)


def _acled_crowd_size(tags: str) -> str | None:
    match = re.search(r"crowd size\s*=\s*([^;]+)", tags, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip() or None


def _acled_tags(row: dict, category_event_type: str) -> list[tuple[str, str]]:
    tags: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value, kind: str) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        tags.append((text, kind))

    add(category_event_type, "keyword")
    add(row.get("sub_event_type"), "keyword")
    add(row.get("disorder_type"), "keyword")
    for part in str(row.get("tags") or "").split(";"):
        add(part, "keyword")
    if (_int(row.get("fatalities")) or 0) > 0:
        add("fatalities", "impact")
    if str(row.get("civilian_targeting") or "").strip():
        add("civilian_targeting", "impact")
    return tags


def prepare_acled_event(row: dict) -> dict | None:
    event_type = str(row.get("event_type") or "").strip()
    category = acled_category(event_type)
    source_id = str(row.get("event_id_cnty") or "").strip()
    lat, lng = _num(row.get("latitude")), _num(row.get("longitude"))
    event_date = str(row.get("event_date") or "").strip()
    if category is None or not source_id or lat is None or lng is None or not event_date:
        return None
    try:
        occurred_at = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    updated_at = None
    timestamp = _int(row.get("timestamp"))
    if timestamp:
        updated_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    location = str(row.get("location") or "").strip()
    country = str(row.get("country") or "").strip()
    subtype = str(row.get("sub_event_type") or "").strip() or None
    place = ", ".join(part for part in (location, country) if part)
    title = f"{subtype or event_type} in {place}" if place else (subtype or event_type)
    fatalities = _int(row.get("fatalities"))
    civilian = str(row.get("civilian_targeting") or "").strip() or None
    entities = []
    for actor in (row.get("actor1"), row.get("actor2")):
        text = str(actor or "").strip()
        if text:
            entities.append({"type": "actor", "text": text, "confidence": 1})
    kind = {
        "event_type": event_type,
        "disorder_type": row.get("disorder_type") or None,
        "fatalities": fatalities,
        "civilian_targeting": civilian,
        "actor1": row.get("actor1") or None,
        "assoc_actor_1": row.get("assoc_actor_1") or None,
        "actor2": row.get("actor2") or None,
        "assoc_actor_2": row.get("assoc_actor_2") or None,
        "inter1": None if row.get("inter1") in (None, "") else str(row.get("inter1")),
        "inter2": None if row.get("inter2") in (None, "") else str(row.get("inter2")),
        "interaction": None if row.get("interaction") in (None, "") else str(row.get("interaction")),
        "time_precision": _int(row.get("time_precision")),
        "geo_precision_code": _int(row.get("geo_precision")),
        "location": location or None,
        "admin1": row.get("admin1") or None,
        "admin2": row.get("admin2") or None,
        "admin3": row.get("admin3") or None,
        "region": row.get("region") or None,
        "country": country or None,
        "iso": _int(row.get("iso")),
        "source_name": row.get("source") or None,
        "source_scale": row.get("source_scale") or None,
        "population_best": _num(row.get("population_best")),
    }
    if category == "protest":
        kind["crowd_size"] = _acled_crowd_size(str(row.get("tags") or ""))
    return {
        "event_id": f"acled:{source_id}",
        "source_event_id": source_id,
        "category": category,
        "subtype": subtype,
        "title": title,
        "summary": row.get("notes") or None,
        "occurred_at": occurred_at,
        "updated_at": updated_at,
        "lng": lng,
        "lat": lat,
        "geo_precision": acled_geo_precision(row.get("geo_precision")),
        "significance": acled_significance(fatalities, civilian),
        "weight": fatalities or 0,
        "entities": entities,
        "kind": kind,
        "tags": _acled_tags(row, event_type),
    }


def _acled_request(url: str, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"User-Agent": "hypothesis-globe/ingest", "Accept": "application/json"}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(data).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        raise RuntimeError(f"http {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError("network") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected response")
    return payload


def _clean_access_token(value: str) -> str:
    """Keep the access-token JWT when the OAuth JSON was pasted after it."""
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    marker = value.find("refresh_token")
    if marker != -1:
        value = value[:marker]
    value = value.strip().strip("\"'{},")
    parts = value.split(".")
    if len(parts) < 3:
        return value
    signature = re.match(r"[A-Za-z0-9_-]+", parts[2])
    if signature:
        parts[2] = signature.group(0)
    return ".".join(parts[:3])


def _acled_access_token() -> tuple[str | None, dict | None]:
    load_repo_env()
    provided = _clean_access_token(os.environ.get("ACLED_ACCESS_TOKEN", ""))
    if provided:
        return provided, None
    username = os.environ.get("ACLED_USERNAME", "").strip()
    password = os.environ.get("ACLED_PASSWORD", "").strip()
    if not username or not password:
        return None, {
            "source": "acled",
            "status": "skipped",
            "detail": "ACLED_ACCESS_TOKEN is empty",
        }
    try:
        token_body = _acled_request(
            ACLED_TOKEN_URL,
            {
                "username": username,
                "password": password,
                "grant_type": "password",
                "client_id": "acled",
                "scope": "authenticated",
            },
        )
    except RuntimeError as exc:
        return None, {"source": "acled", "status": "error", "detail": f"ACLED login failed ({exc})"}
    token = str(token_body.get("access_token") or "")
    if not token:
        return None, {"source": "acled", "status": "error", "detail": "ACLED login failed (no token)"}
    return token, None


def _fetch_acled_rows() -> tuple[list[dict] | None, dict | None, dict | None]:
    token, problem = _acled_access_token()
    if problem:
        return None, None, problem
    end = _now().date()
    start = end - timedelta(days=6)
    window = {"start": start.isoformat(), "end": end.isoformat()}
    rows: list[dict] = []
    page = 1
    try:
        # Monadic keeps one row per event and still uses page numbers.
        # Cursor pagination is for dyadic exports and is not sent here.
        while len(rows) < ACLED_CAP and page <= 10:
            params = {
                "_format": "json",
                "export_type": "monadic",
                "event_date": f"{window['start']}|{window['end']}",
                "event_date_where": "BETWEEN",
                "population": "TRUE",
                "limit": str(min(ACLED_PAGE_SIZE, ACLED_CAP - len(rows))),
                "page": str(page),
            }
            payload = _acled_request(ACLED_READ_URL + "?" + urlencode(params), token=token)
            if payload.get("success") is False:
                status = payload.get("status") or "error"
                return None, None, {"source": "acled", "status": "error", "detail": f"ACLED read failed ({status})"}
            batch = payload.get("data") or []
            if isinstance(batch, dict):
                batch = [batch]
            rows.extend(item for item in batch if isinstance(item, dict))
            if not batch or len(batch) < int(params["limit"]):
                break
            page += 1
    except RuntimeError as exc:
        return None, None, {"source": "acled", "status": "error", "detail": f"ACLED read failed ({exc})"}
    return rows[:ACLED_CAP], window, None


def _upsert_acled_kind(cur, category: str, event_id: str, kind: dict) -> None:
    fields = list(_ACLED_KIND_FIELDS)
    if category == "protest":
        fields.append("crowd_size")
    columns = ", ".join(["event_id", *fields])
    placeholders = ", ".join(["%s"] * (len(fields) + 1))
    updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in fields)
    table = _ACLED_KIND_TABLE[category]
    cur.execute(
        f"""
        INSERT INTO {table} ({columns})
        VALUES ({placeholders})
        ON CONFLICT (event_id) DO UPDATE SET {updates}
        """,
        [event_id, *[kind.get(name) for name in fields]],
    )


def load_acled(cur, rows: list[dict], window: dict) -> dict:
    prepared = [item for item in (prepare_acled_event(row) for row in rows) if item]
    counts = {category: 0 for category in _ACLED_KIND_TABLE}
    batch_id = _record_batch(cur, "acled", {"window": window, "kept": len(prepared), "cap": ACLED_CAP})
    for item in prepared:
        event_id = item["event_id"]
        cur.execute(
            _EVENT_UPSERT,
            _blank_event(
                event_id=event_id,
                source="acled",
                source_event_id=item["source_event_id"],
                category=item["category"],
                subtype=item["subtype"],
                title=item["title"],
                summary=item["summary"],
                info_url=ACLED_INFO_URL,
                occurred_at=item["occurred_at"],
                updated_at=item["updated_at"],
                lng=item["lng"],
                lat=item["lat"],
                geo_precision=item["geo_precision"],
                geo_source="native",
                significance=item["significance"],
                weight=item["weight"],
                entities=json.dumps(item["entities"]),
                raw_ref=f"raw.ingest_batch:{batch_id}",
            ),
        )
        _upsert_acled_kind(cur, item["category"], event_id, item["kind"])
        cur.execute("DELETE FROM mart.event_tag WHERE event_id = %s", (event_id,))
        for tag, tag_kind in item["tags"]:
            cur.execute(
                """
                INSERT INTO mart.event_tag (event_id, tag, tag_kind)
                VALUES (%s, %s, %s)
                ON CONFLICT (event_id, tag) DO NOTHING
                """,
                (event_id, tag, tag_kind),
            )
        counts[item["category"]] += 1
    return {"source": "acled", "status": "ok", "events": len(prepared), **counts}


CONFLICT_CSV_PATH = REPO_ROOT / "data" / "backup_csvs" / "finalconflictCSV.csv"
CONFLICT_CSV_CATEGORIES = {"conflict", "protest", "terror", "politics", "crime"}


def conflict_csv_significance(goldstein) -> float:
    magnitude = abs(_num(goldstein) or 0)
    return min(100.0, 55.0 + magnitude * 4.5)


def conflict_csv_geo_precision(place: str) -> str:
    if "(general)" in place.lower() or "," not in place:
        return "region"
    return "city"


def prepare_conflict_csv_row(row: dict) -> dict | None:
    category = str(row.get("category") or "").strip()
    url = str(row.get("SOURCEURL") or "").strip()
    title = str(row.get("title") or "").strip()
    lat, lng = _num(row.get("ActionGeo_Lat")), _num(row.get("ActionGeo_Long"))
    if category not in CONFLICT_CSV_CATEGORIES or not url or not title or lat is None or lng is None:
        return None
    try:
        occurred_at = datetime.strptime(str(row.get("SQLDATE") or "").strip(), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    place = str(row.get("ActionGeo_FullName") or "").strip()
    actors = []
    for name in (row.get("Actor1Name"), row.get("Actor2Name")):
        text = str(name or "").strip()
        if text:
            actors.append({"type": "actor", "text": text, "confidence": 1})
    tags = [(category, "keyword")]
    event_code = str(row.get("EventCode") or "").strip()
    if event_code:
        tags.append((event_code, "keyword"))
    kind = None
    if category in {"conflict", "protest"}:
        kind = {name: None for name in _ACLED_KIND_FIELDS}
        kind["event_type"] = category
        kind["actor1"] = str(row.get("Actor1Name") or "").strip() or None
        kind["actor2"] = str(row.get("Actor2Name") or "").strip() or None
        kind["location"] = place or None
    return {
        "event_id": f"conflict-csv:{url}",
        "source_event_id": url,
        "category": category,
        "subtype": event_code or None,
        "title": title,
        "summary": str(row.get("description") or "").strip() or None,
        "info_url": url,
        "occurred_at": occurred_at,
        "lng": lng,
        "lat": lat,
        "geo_precision": conflict_csv_geo_precision(place),
        "significance": conflict_csv_significance(row.get("GoldsteinScale")),
        "weight": _int(row.get("NumArticles")) or 0,
        "entities": actors,
        "tags": tags,
        "kind": kind,
    }


def read_conflict_csv(path: Path | None = None) -> list[dict]:
    csv_path = path or CONFLICT_CSV_PATH
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_conflict_csv(cur, rows: list[dict] | None = None) -> dict:
    if rows is None:
        if not CONFLICT_CSV_PATH.is_file():
            return {"source": "conflict_csv", "status": "skipped", "detail": "finalconflictCSV.csv is missing"}
        rows = read_conflict_csv()
    prepared = [item for item in (prepare_conflict_csv_row(row) for row in rows) if item]
    counts = {category: 0 for category in sorted(CONFLICT_CSV_CATEGORIES)}
    batch_id = _record_batch(cur, "conflict_csv", {"file": CONFLICT_CSV_PATH.name, "kept": len(prepared)})
    for item in prepared:
        event_id = item["event_id"]
        cur.execute(
            _EVENT_UPSERT,
            _blank_event(
                event_id=event_id,
                source="conflict_csv",
                source_event_id=item["source_event_id"],
                category=item["category"],
                subtype=item["subtype"],
                title=item["title"],
                summary=item["summary"],
                info_url=item["info_url"],
                occurred_at=item["occurred_at"],
                updated_at=item["occurred_at"],
                lng=item["lng"],
                lat=item["lat"],
                geo_precision=item["geo_precision"],
                geo_source="native",
                significance=item["significance"],
                weight=item["weight"],
                entities=json.dumps(item["entities"]),
                raw_ref=f"raw.ingest_batch:{batch_id}",
            ),
        )
        if item["kind"] is not None:
            _upsert_acled_kind(cur, item["category"], event_id, item["kind"])
        cur.execute("DELETE FROM mart.event_tag WHERE event_id = %s", (event_id,))
        for tag, tag_kind in item["tags"]:
            cur.execute(
                """
                INSERT INTO mart.event_tag (event_id, tag, tag_kind)
                VALUES (%s, %s, %s)
                ON CONFLICT (event_id, tag) DO NOTHING
                """,
                (event_id, tag, tag_kind),
            )
        counts[item["category"]] += 1
    return {"source": "conflict_csv", "status": "ok", "events": len(prepared), **counts}


def run_ingest() -> dict:
    load_repo_env()
    if not database_configured():
        return {
            "ok": False,
            "status": "needs_database_url",
            "detail": "Paste DATABASE_URL into the gitignored .env file, then run this again.",
        }
    import psycopg

    acled_rows, acled_window, acled_problem = _fetch_acled_rows()
    summary = []
    conn = psycopg.connect(database_url(), connect_timeout=20)
    try:
        if acled_problem:
            summary.append(acled_problem)
        else:
            with conn.transaction():
                with conn.cursor() as cur:
                    summary.append(load_acled(cur, acled_rows or [], acled_window or {}))
        with conn.transaction():
            with conn.cursor() as cur:
                summary.append(load_conflict_csv(cur))
        # Thumbnails last, outside the loaders' transactions: only rows still without an
        # image are looked up, so repeat runs are cheap. A failure here never fails ingest.
        try:
            from jobs.ingest.images import fill_missing_images

            images = fill_missing_images(conn, sources=("conflict_csv",), log=lambda _line: None)
            summary.append({"source": "images", "status": "ok", **images})
        except Exception as exc:  # noqa: BLE001 — images are decoration; keep the loaded events
            conn.rollback()
            summary.append({"source": "images", "status": "error", "detail": str(exc)[:200]})
        return {"ok": True, "status": "loaded", "sources": summary}
    finally:
        conn.close()


def main() -> None:
    result = run_ingest()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
