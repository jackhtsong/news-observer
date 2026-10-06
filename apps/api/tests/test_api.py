"""Track C contract checks. No network, credentials, or repository cache writes."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from db import DatabasePing


def _event(event_id: str, layer_id: str, significance: float, occurred_at: str, lat: float, lng: float) -> dict:
    """A minimal schema-valid Event, so tests do not depend on the bundled sample data."""
    return {
        "id": event_id, "source": "conflict_csv" if layer_id != "news" else "news",
        "sourceUrl": f"https://example.com/{event_id}", "imageUrl": None, "layerId": layer_id, "subtype": None,
        "title": f"Test {layer_id} event", "summary": None, "occurredAt": occurred_at, "updatedAt": occurred_at,
        "endedAt": None, "lng": lng, "lat": lat, "altM": None, "geoPrecision": "city", "geoSource": "native",
        "weight": None, "significance": significance, "countryIso3": None, "keywords": [], "entities": [],
        "rawRef": None, "tags": [], "attributes": {},
    }


EVENTS = [
    _event("test:conflict", "conflict", 80, "2026-10-03T12:04:11Z", 38.2, -122.3),
    _event("test:protest", "protest", 55, "2026-10-03T11:40:00Z", 38.5, -122.4),
    _event("test:news", "news", 40, "2026-10-03T13:20:00Z", 38.3, -122.3),
]
LINKS = [{
    "id": "link:test:news:test:conflict", "sourceId": "test:news", "targetId": "test:conflict",
    "relation": "primary-story", "confidence": 0.74, "rationale": "The article reports the clash.",
    "citations": [], "model": "test",
}]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.events = json.loads(json.dumps(EVENTS))
        self.links = json.loads(json.dumps(LINKS))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.enterContext(patch.dict(os.environ, {"DATABASE_URL": ""}))
        self.enterContext(patch.object(main, "_last_ingest_at", None))
        for name, relative in {
            "SNAPSHOTS_DIR": "snapshots",
            "EVENTS_SNAPSHOT": "snapshots/events.json",
            "LINKS_SNAPSHOT": "snapshots/links.json",
            "SNAPSHOT_META": "snapshots/meta.json",
            "EVENTS_FIXTURE": "fixtures/events.json",
            "LINKS_FIXTURE": "fixtures/links.json",
        }.items():
            self.enterContext(patch.object(main, name, root / relative))
        main.EVENTS_FIXTURE.parent.mkdir()
        main.EVENTS_FIXTURE.write_text(json.dumps(self.events))
        main.LINKS_FIXTURE.write_text(json.dumps(self.links))
        self.client = self.enterContext(TestClient(main.app, raise_server_exceptions=False))

    def test_fixture_contract_detail_and_links(self):
        response = self.client.get("/events")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["events"], self.events)
        self.assertEqual(set(body), {"generatedAt", "sourceStatus", "events", "nextCursor"})
        event_id = self.events[0]["id"]
        self.assertEqual(self.client.get(f"/events/{event_id}").json(), self.events[0])
        self.assertEqual(self.client.get(f"/events/{event_id}/links").json(), self.links)
        for path in ("/events/missing", "/events/missing/links"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_filters_and_exclusive_end(self):
        response = self.client.get("/events", params={"types": "conflict,protest", "minSignificance": 60})
        self.assertEqual(response.json()["events"], [self.events[0]])
        timestamp = self.events[0]["occurredAt"]
        response = self.client.get("/events", params={"types": "conflict", "start": timestamp})
        self.assertEqual(response.json()["events"], [self.events[0]])
        response = self.client.get("/events", params={"types": "conflict", "end": timestamp})
        self.assertEqual(response.json()["events"], [])

    def test_invalid_queries_are_client_errors(self):
        for params in (
            {"start": "not-a-date"},
            {"start": "2026-10-03T12:00:00"},
            {"start": "2026-10-04T00:00:00Z", "end": "2026-10-03T00:00:00Z"},
            {"limit": 0},
            {"limit": 8001},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/events", params=params).status_code, 422)

    def test_cors(self):
        response = self.client.options("/events", headers={
            "Origin": main.DEFAULT_VITE_ORIGIN,
            "Access-Control-Request-Method": "GET",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], main.DEFAULT_VITE_ORIGIN)

    def test_forced_fixture_mode_skips_warehouse(self):
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events") as fetch:
            self.assertEqual(self.client.get("/events?fixture=1").json()["events"], self.events)
            fetch.assert_not_called()

    def test_live_filter_and_small_limit_preserve_fallback_dataset(self):
        healthy = DatabasePing("ok", "2026-10-03T14:00:00Z")
        dark = DatabasePing("dark", None, "simulated outage")

        def fetch(*, limit=None):
            return self.events[:limit], healthy

        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", side_effect=fetch):
            response = self.client.get("/events?types=protest&limit=1")
            self.assertEqual(response.json()["events"], [self.events[1]])
            self.assertEqual(json.loads(main.EVENTS_SNAPSHOT.read_text()), self.events)
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(None, dark)):
            response = self.client.get("/events")
            self.assertEqual(response.json()["events"], self.events)
            self.assertEqual(response.json()["sourceStatus"]["database"], "dark")

    def test_corrupt_snapshot_falls_back_to_fixtures(self):
        main.SNAPSHOTS_DIR.mkdir()
        dark = DatabasePing("dark", None, "simulated outage")
        for content in ("[", "[{}]", '["invalid event"]'):
            with self.subTest(content=content):
                main.EVENTS_SNAPSHOT.write_text(content)
                with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(None, dark)):
                    response = self.client.get("/events")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["events"], self.events)

    def test_bad_live_rows_do_not_replace_last_good_snapshot(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.EVENTS_SNAPSHOT.write_text(json.dumps(self.events))
        healthy = DatabasePing("ok", None)
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=([{}], healthy)):
            response = self.client.get("/events")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["events"], self.events)
            self.assertEqual(response.json()["sourceStatus"]["database"], "error")
            self.assertEqual(json.loads(main.EVENTS_SNAPSHOT.read_text()), self.events)

    def test_health_fails_when_fixture_cache_is_unreadable(self):
        for content in ("[", "[{}]"):
            with self.subTest(content=content):
                main.EVENTS_FIXTURE.write_text(content)
                self.assertFalse(self.client.get("/health").json()["ok"])
                self.assertEqual(self.client.get("/events").status_code, 503)

    def test_outage_without_cache_names_tigerdata(self):
        main.EVENTS_FIXTURE.unlink()
        dark = DatabasePing("dark", None, "simulated outage")
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(None, dark)), patch.object(main, "ping_and_warmup", return_value=dark):
            self.assertFalse(self.client.get("/health").json()["ok"])
            response = self.client.get("/events")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["failingSource"], "database")

    def test_fixture_sources_are_not_reported_as_live(self):
        status = self.client.get("/events").json()["sourceStatus"]
        self.assertEqual(status, {"news": "fixture", "gdelt": "fixture", "database": "fixture"})

    def test_cursor_pages_all_results_and_rejects_changed_data(self):
        first = self.client.get("/events?limit=1").json()
        second = self.client.get("/events", params={"limit": 1, "cursor": first["nextCursor"]}).json()
        third = self.client.get("/events", params={"limit": 1, "cursor": second["nextCursor"]}).json()
        self.assertEqual(first["events"] + second["events"] + third["events"], self.events)
        self.assertIsNone(third["nextCursor"])
        main.EVENTS_FIXTURE.write_text(json.dumps(self.events[:1]))
        self.assertEqual(self.client.get("/events", params={"cursor": first["nextCursor"]}).status_code, 409)
        self.assertEqual(self.client.get("/events?cursor=invalid").status_code, 422)

    def test_health_checks_mart_after_successful_ping(self):
        healthy = DatabasePing("ok", None)
        unavailable = DatabasePing("error", None, "MART.EVENT missing")
        with patch.object(main, "ping_and_warmup", return_value=healthy), patch.object(main, "fetch_mart_events", return_value=(None, unavailable)):
            body = self.client.get("/health").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["database"], "error")
        self.assertEqual(body["dataSource"], "fixture")
        self.assertIn("MART.EVENT", body["detail"])

    def test_health_rejects_unusable_mart_with_no_cache(self):
        main.EVENTS_FIXTURE.unlink()
        healthy = DatabasePing("ok", None)
        with patch.object(main, "ping_and_warmup", return_value=healthy), patch.object(main, "fetch_mart_events", return_value=([{}], healthy)):
            body = self.client.get("/health").json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["dataSource"], "unavailable")

    def test_successful_empty_warehouse_remains_empty(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.EVENTS_SNAPSHOT.write_text(json.dumps(self.events))
        healthy = DatabasePing("ok", None)
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=([], healthy)), patch.object(main, "ping_and_warmup", return_value=healthy):
            body = self.client.get("/events").json()
            health = self.client.get("/health").json()
        self.assertEqual(body["events"], [])
        self.assertEqual(body["sourceStatus"]["database"], "ok")
        self.assertEqual(json.loads(main.EVENTS_SNAPSHOT.read_text()), [])
        self.assertTrue(health["ok"])
        self.assertEqual(health["dataSource"], "warehouse")

    def test_live_links_are_cached_for_an_outage(self):
        healthy = DatabasePing("ok", None)
        dark = DatabasePing("dark", None, "simulated outage")
        link = {**self.links[0], "id": "link:live", "rationale": "Warehouse relation"}
        path = f"/events/{self.events[0]['id']}/links"
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(self.events, healthy)), patch.object(main, "fetch_mart_links", return_value=([link], healthy)):
            self.assertEqual(self.client.get(path).json(), [link])
        main.LINKS_FIXTURE.unlink()
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(None, dark)):
            self.assertEqual(self.client.get(path).json(), [link])

    def test_all_links_endpoint_reads_once_and_falls_back(self):
        self.assertEqual(self.client.get("/links").json(), self.links)
        healthy = DatabasePing("ok", None)
        dark = DatabasePing("dark", None, "simulated outage")
        link = {**self.links[0], "id": "link:live"}
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_links", return_value=([link], healthy)), patch.object(main, "fetch_mart_events") as events:
            self.assertEqual(self.client.get("/links").json(), [link])
            events.assert_not_called()
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_links", return_value=(None, dark)):
            self.assertEqual(self.client.get("/links").json(), [link])

    def test_invalid_live_links_use_existing_cache(self):
        healthy = DatabasePing("ok", None)
        path = f"/events/{self.events[0]['id']}/links"
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(self.events, healthy)), patch.object(main, "fetch_mart_links", return_value=([{}], healthy)):
            self.assertEqual(self.client.get(path).json(), self.links)
        self.assertFalse(main.LINKS_SNAPSHOT.exists())

    def test_source_metadata_survives_snapshot_refresh(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.SNAPSHOT_META.write_text(json.dumps({
            "lastIngestAt": "2026-10-03T14:00:00Z",
            "sourceStatus": {"news": "ok", "gdelt": "error"},
        }))
        healthy = DatabasePing("ok", None)
        with patch.dict(os.environ, {"DATABASE_URL": "configured"}), patch.object(main, "fetch_mart_events", return_value=(self.events, healthy)):
            status = self.client.get("/events").json()["sourceStatus"]
        self.assertEqual(status, {"news": "ok", "gdelt": "error", "database": "ok"})
        self.assertEqual(json.loads(main.SNAPSHOT_META.read_text())["sourceStatus"]["gdelt"], "error")

    def test_corrupt_metadata_does_not_break_health(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.SNAPSHOT_META.write_bytes(b"\xff")
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_snapshot_write_failure_preserves_previous_file(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.EVENTS_SNAPSHOT.write_text(json.dumps(self.events))
        with patch.object(Path, "replace", side_effect=OSError("simulated write failure")):
            main._write_snapshot([], last_ingest_at=None)
        self.assertEqual(json.loads(main.EVENTS_SNAPSHOT.read_text()), self.events)
        self.assertEqual(list(main.SNAPSHOTS_DIR.iterdir()), [main.EVENTS_SNAPSHOT])

    def test_outage_health_uses_snapshot_ingest_time(self):
        main.SNAPSHOTS_DIR.mkdir()
        main.SNAPSHOT_META.write_text('{"lastIngestAt":"2026-10-03T14:00:00Z"}')
        dark = DatabasePing("dark", None, "simulated outage")
        with patch.object(main, "ping_and_warmup", return_value=dark):
            body = self.client.get("/health").json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["lastIngestAt"], "2026-10-03T14:00:00Z")
        self.assertEqual(body["database"], "dark")


if __name__ == "__main__":
    unittest.main()
