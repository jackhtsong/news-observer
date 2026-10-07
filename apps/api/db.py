"""Postgres (Neon) access for health checks and event reads.

Fixture mode needs no DATABASE_URL. The connector imports only when a URL is set.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
# Only categories the shared Event schema still lists are served; rows of retired
# categories (such as the former natural-hazard layers) stay out of the API.
LAYER_IDS: list[str] = json.loads(
    (REPO_ROOT / "packages" / "schema" / "event.schema.json").read_text(encoding="utf-8")
)["properties"]["layerId"]["enum"]

_EVENT_SQL = """
SELECT
  e.event_id AS id,
  e.source,
  e.info_url AS source_url,
  e.image_url,
  e.category AS layer_id,
  e.subtype,
  e.title,
  e.summary,
  e.occurred_at,
  e.updated_at,
  e.ended_at,
  e.lng,
  e.lat,
  e.alt_m,
  e.geo_precision,
  e.geo_source,
  e.weight,
  e.significance,
  e.country_iso3,
  COALESCE(e.keywords, '[]'::jsonb) AS keywords,
  COALESCE(e.entities, '[]'::jsonb) AS entities,
  e.raw_ref,
  COALESCE(
    (
      SELECT jsonb_agg(t.tag ORDER BY t.tag)
      FROM mart.event_tag t
      WHERE t.event_id = e.event_id
    ),
    '[]'::jsonb
  ) AS tags,
  COALESCE(
    CASE e.category
      WHEN 'conflict' THEN (to_jsonb(cf) - 'event_id')
      WHEN 'protest' THEN (to_jsonb(pr) - 'event_id')
      WHEN 'strategic_development' THEN (to_jsonb(sd) - 'event_id')
      ELSE '{}'::jsonb
    END,
    '{}'::jsonb
  ) AS attributes
FROM mart.event e
LEFT JOIN mart.conflict cf ON cf.event_id = e.event_id
LEFT JOIN mart.protest pr ON pr.event_id = e.event_id
LEFT JOIN mart.strategic_development sd ON sd.event_id = e.event_id
WHERE e.category = ANY(%(layers)s)
"""


@dataclass
class DatabasePing:
    status: str  # ok | dark | error | fixture
    last_ingest_at: str | None
    detail: str | None = None
    warmup_ms: int | None = None


def load_repo_env() -> None:
    """Process environment wins, followed by .env, then legacy .env.local."""
    from dotenv import load_dotenv

    for name in (".env", ".env.local"):
        load_dotenv(REPO_ROOT / name, override=False)


def database_url() -> str:
    load_repo_env()
    return os.environ.get("DATABASE_URL", "").strip()


def database_configured() -> bool:
    return bool(database_url())


def _iso_z(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


def _failure(what: str, exc: Exception) -> str:
    """A safe public detail (error type only), with the full error in the server log.

    The log line masks any password in a connection string, so it is safe in Vercel's logs;
    the response never carries the message, which can name the host or user.
    """
    message = re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", str(exc))
    print(f"database {what}: {type(exc).__name__}: {message}", file=sys.stderr)
    reason = next((label for phrase, label in _KNOWN_FAILURES if phrase in message.lower()), None)
    return f"Database {what} ({type(exc).__name__}{': ' + reason if reason else ''})"


# Common connection failures, named in /health without any host, user, or password.
_KNOWN_FAILURES = (
    ("password authentication failed", "password authentication failed"),
    ("could not translate host name", "host name not found; check the connection string"),
    ("name or service not known", "host name not found; check the connection string"),
    ("invalid dsn", "malformed connection string"),
    ("invalid connection option", "malformed connection string"),
    ("missing \"=\"", "malformed connection string"),
    ("timeout expired", "connection timed out"),
    ("timed out", "connection timed out"),
    ("does not exist", "database, role, or endpoint does not exist"),
    ("endpoint is disabled", "Neon endpoint is disabled"),
    ("ssl", "TLS/SSL negotiation failed"),
    ("connection refused", "connection refused"),
)


def _ipv4_hostaddr(url: str) -> dict[str, str]:
    """{"hostaddr": IPv4} for the URL's host name, or {} when there is none to pin.

    Vercel functions cannot open outbound IPv6 connections, and Neon's host names resolve to
    IPv6 as well as IPv4, so a connection may try the IPv6 address and fail ("Cannot assign
    requested address"). Pinning the IPv4 address avoids that; libpq still sends `host` for
    TLS verification and SNI, which Neon uses to route to the right endpoint.
    """
    import socket

    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(url)
    host, port = str(params.get("host") or ""), str(params.get("port") or "5432")
    if not host or params.get("hostaddr") or "," in host + port or host.startswith("/") or not port.isdigit():
        return {}  # already pinned, several hosts, a Unix socket, or no host
    try:
        socket.inet_pton(socket.AF_INET, host)
        return {}  # already an IPv4 literal
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return {}  # let libpq report the lookup failure itself
    return {"hostaddr": infos[0][4][0]} if infos else {}


def _connect():
    import psycopg

    url = database_url()
    return psycopg.connect(url, connect_timeout=5, sslmode="require",
                           options="-c statement_timeout=10000", **_ipv4_hostaddr(url))


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _clean_attributes(value: Any) -> dict[str, Any]:
    data = _json_value(value) or {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in data.items():
        if item is None or key == "event_id":
            continue
        if isinstance(item, datetime):
            item = _iso_z(item)
        cleaned[key] = item
    return cleaned


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return _iso_z(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _row_to_event(row: dict[str, Any]) -> dict[str, Any]:
    keywords = _json_value(row.get("keywords")) or []
    entities = _json_value(row.get("entities")) or []
    tags = _json_value(row.get("tags")) or []
    return _json_safe({
        "id": row["id"],
        "source": row["source"],
        "sourceUrl": row.get("source_url"),
        "imageUrl": row.get("image_url"),
        "layerId": row["layer_id"],
        "subtype": row.get("subtype"),
        "title": row["title"],
        "summary": row.get("summary"),
        "occurredAt": _iso_z(row.get("occurred_at")),
        "updatedAt": _iso_z(row.get("updated_at")) or _iso_z(row.get("occurred_at")),
        "endedAt": _iso_z(row.get("ended_at")),
        "lng": row["lng"],
        "lat": row["lat"],
        "altM": row.get("alt_m"),
        "geoPrecision": row["geo_precision"],
        "geoSource": row["geo_source"],
        "weight": row.get("weight"),
        "significance": row["significance"],
        "countryIso3": row.get("country_iso3"),
        "keywords": keywords if isinstance(keywords, list) else [],
        "entities": entities if isinstance(entities, list) else [],
        "rawRef": row.get("raw_ref"),
        "tags": tags if isinstance(tags, list) else [],
        "attributes": _clean_attributes(row.get("attributes")),
    })


def ping_and_warmup(*, fetch_last_ingest: bool = True) -> DatabasePing:
    if not database_configured():
        return DatabasePing(
            status="fixture",
            last_ingest_at=None,
            detail="DATABASE_URL unset; fixture mode",
        )

    started = time.perf_counter()
    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001 — surface network and auth failures
        return DatabasePing(
            status="dark",
            last_ingest_at=None,
            detail=_failure("connection unavailable", exc),
        )

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            warmup_ms = int((time.perf_counter() - started) * 1000)
            last_ingest_at = None
            if fetch_last_ingest:
                try:
                    cur.execute("SELECT MAX(pulled_at) FROM raw.ingest_batch")
                    row = cur.fetchone()
                    last_ingest_at = _iso_z(row[0] if row else None)
                except Exception:  # noqa: BLE001 — schema may not be applied yet
                    conn.rollback()
                    last_ingest_at = None
            return DatabasePing(
                status="ok",
                last_ingest_at=last_ingest_at,
                warmup_ms=warmup_ms,
            )
    except Exception as exc:  # noqa: BLE001
        return DatabasePing(
            status="dark",
            last_ingest_at=None,
            detail=_failure("warmup failed", exc),
            warmup_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        conn.close()


def fetch_mart_events(*, ping: DatabasePing | None = None) -> tuple[list[dict[str, Any]] | None, DatabasePing]:
    ping = ping or ping_and_warmup(fetch_last_ingest=True)
    if ping.status != "ok":
        return None, ping

    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001
        return None, DatabasePing(
            status="dark",
            last_ingest_at=ping.last_ingest_at,
            detail=_failure("connection unavailable on event read", exc),
            warmup_ms=ping.warmup_ms,
        )

    try:
        with conn.cursor() as cur:
            cur.execute(
                _EVENT_SQL + "\nORDER BY e.occurred_at DESC, e.event_id",
                {"layers": LAYER_IDS},
            )
            columns = [col.name for col in cur.description]
            events = [_row_to_event(dict(zip(columns, row, strict=True))) for row in cur.fetchall()]
            return events, ping
    except Exception as exc:  # noqa: BLE001
        return None, DatabasePing(
            status="error",
            last_ingest_at=ping.last_ingest_at,
            detail="mart.event unavailable",
            warmup_ms=ping.warmup_ms,
        )
    finally:
        conn.close()


def fetch_links_for_event(event_id: str | None) -> list[dict[str, Any]] | None:
    """Return links touching event_id, or None when the database cannot be read."""
    if not database_configured():
        return None
    try:
        conn = _connect()
    except Exception:  # noqa: BLE001
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT link_id, source_id, target_id, relation, confidence,
                       rationale, citations, model
                FROM mart.event_link
                WHERE %s::text IS NULL OR source_id = %s OR target_id = %s
                ORDER BY link_id
                """,
                (event_id, event_id, event_id),
            )
            links = []
            for link_id, source_id, target_id, relation, confidence, rationale, citations, model in cur.fetchall():
                parsed = _json_value(citations)
                links.append(_json_safe({
                    "id": link_id,
                    "sourceId": source_id,
                    "targetId": target_id,
                    "relation": relation,
                    "confidence": confidence,
                    "rationale": rationale or "",
                    "citations": parsed if isinstance(parsed, (list, dict)) else [],
                    "model": model or "",
                }))
            return links
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()


def fetch_mart_links() -> tuple[list[dict[str, Any]] | None, DatabasePing]:
    links = fetch_links_for_event(None)
    return links, DatabasePing("ok" if links is not None else "error", None,
                              None if links is not None else "mart.event_link unavailable")
