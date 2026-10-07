"""Exercise the shared PR #30 adapter without accessing the shared database."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import db


class DatabaseTests(unittest.TestCase):
    def test_environment_precedence_and_legacy_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text('DATABASE_URL="postgresql://primary.invalid/db"\n')
            (root / ".env.local").write_text('DATABASE_URL=legacy\nGOOGLE_API_KEY=test-only\n')
            with patch.object(db, "REPO_ROOT", root), patch.dict(os.environ, {}, clear=True):
                db.load_repo_env()
                self.assertEqual(db.database_url(), "postgresql://primary.invalid/db")
                self.assertEqual(os.environ["GOOGLE_API_KEY"], "test-only")
                os.environ["DATABASE_URL"] = "process-wins"
                db.load_repo_env()
                self.assertEqual(db.database_url(), "process-wins")

    def test_event_read_reuses_ping_and_does_not_truncate(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.description = []
        cursor.fetchall.return_value = []
        healthy = db.DatabasePing("ok", None)
        with patch.object(db, "_connect", return_value=connection), patch.object(db, "ping_and_warmup") as ping:
            self.assertEqual(db.fetch_mart_events(ping=healthy), ([], healthy))
        ping.assert_not_called()
        sql = cursor.execute.call_args.args[0]
        self.assertNotIn("LIMIT", sql)
        self.assertIn("e.event_id", sql)
        self.assertNotIn("mart.hazard_exposure", sql)
        self.assertIn("e.category = ANY(%(layers)s)", sql)
        self.assertEqual(cursor.execute.call_args.args[1], {"layers": db.LAYER_IDS})
        self.assertNotIn("earthquake", db.LAYER_IDS)
        connection.close.assert_called_once()

    def test_connection_failures_do_not_disclose_credentials(self):
        with patch.object(db, "database_configured", return_value=True), patch.object(
            db, "_connect", side_effect=RuntimeError("postgresql://user:private@host/db")
        ):
            result = db.ping_and_warmup()
        self.assertEqual(result.status, "dark")
        self.assertNotIn("private", result.detail)

    def test_all_links_are_read_for_complete_snapshot(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("link:1", "a", "b", "related", Decimal("0.9"), "Reason", [], "model")]
        with patch.object(db, "database_configured", return_value=True), patch.object(db, "_connect", return_value=connection):
            links, ping = db.fetch_mart_links()
        self.assertEqual(ping.status, "ok")
        self.assertEqual(cursor.execute.call_args.args[1], (None, None, None))
        self.assertEqual(links[0]["confidence"], 0.9)
        json.dumps(links)

    def test_postgres_numeric_and_nested_attributes_are_json_serializable(self):
        row = {
            "id": "test", "source": "conflict_csv", "layer_id": "conflict", "title": "Test",
            "lng": Decimal("1.2"), "lat": Decimal("3.4"), "significance": Decimal("5"),
            "geo_precision": "point", "geo_source": "native",
            "occurred_at": datetime(2026, 10, 3, tzinfo=timezone.utc),
            "attributes": {"depth": Decimal("6.7")},
        }
        event = db._row_to_event(row)
        self.assertEqual(event["attributes"]["depth"], 6.7)
        json.dumps(event)

    def test_connection_requires_tls_and_bounded_queries(self):
        with patch.object(db, "database_url", return_value="postgresql://test.invalid/db"), patch("psycopg.connect") as connect:
            db._connect()
        self.assertEqual(connect.call_args.kwargs["sslmode"], "require")
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 5)
        # No startup options: Neon's pooled URLs reject them. The limit is set after connecting.
        self.assertNotIn("options", connect.call_args.kwargs)
        self.assertIsNone(connect.call_args.kwargs["prepare_threshold"])
        connect.return_value.execute.assert_called_once_with("SET statement_timeout = 10000")

    def test_connection_pins_an_ipv4_address_for_host_names(self):
        # Vercel functions have no outbound IPv6; Neon host names resolve to both families.
        url = "postgresql://user:pw@ep-x.us-east-2.aws.neon.tech/neondb?sslmode=require"
        ipv4 = [(2, 1, 6, "", ("3.21.0.10", 5432))]
        with patch.object(db, "database_url", return_value=url), patch("psycopg.connect") as connect, \
                patch("socket.getaddrinfo", return_value=ipv4) as lookup:
            db._connect()
        self.assertEqual(connect.call_args.kwargs["hostaddr"], "3.21.0.10")
        self.assertEqual(connect.call_args.args[0], url, "host stays in the URL for TLS and SNI")
        self.assertEqual(lookup.call_args.args[:3], ("ep-x.us-east-2.aws.neon.tech", 5432, 2))
        for unpinned in ("postgresql://u:p@10.0.0.5/db", "postgresql://u:p@h1,h2/db", "postgresql:///db?host=/tmp",
                         "postgresql://u:p@h/db?hostaddr=1.2.3.4"):
            self.assertEqual(db._ipv4_hostaddr(unpinned), {}, unpinned)
        with patch("socket.getaddrinfo", side_effect=OSError("no such host")):
            self.assertEqual(db._ipv4_hostaddr(url), {}, "lookup failures are left for libpq to report")
