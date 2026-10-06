"""GDELT daily loader: event filtering, categories, page headlines, and the loader row shape. No network."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from jobs.ingest import gdelt  # noqa: E402
from jobs.ingest.run import prepare_conflict_csv_row  # noqa: E402


def row(url, code="190", articles=30, country="IS", geo_type="4", day="20261005", root="1", lat="31.5", lng="34.4"):
    cells = [""] * gdelt.COLUMN_COUNT
    cells[gdelt.SQLDATE], cells[gdelt.IS_ROOT], cells[gdelt.EVENT_CODE] = day, root, code
    cells[gdelt.ROOT_CODE], cells[gdelt.NUM_ARTICLES], cells[gdelt.GEO_TYPE] = code[:2], str(articles), geo_type
    cells[gdelt.GEO_COUNTRY], cells[gdelt.GEO_LAT], cells[gdelt.GEO_LNG], cells[gdelt.SOURCE_URL] = country, lat, lng, url
    cells[gdelt.GEO_NAME], cells[gdelt.GOLDSTEIN], cells[gdelt.ACTOR1_NAME] = "Gaza, Israel", "-10", "MILITANT"
    return cells


class GdeltTests(unittest.TestCase):
    def test_cameo_codes_map_to_conflict_layers(self):
        self.assertEqual(gdelt.category_for("145"), "protest")
        self.assertEqual(gdelt.category_for("1832"), "terror")
        self.assertEqual(gdelt.category_for("1823"), "crime")
        self.assertEqual(gdelt.category_for("173"), "crime")
        self.assertEqual(gdelt.category_for("195"), "conflict")
        self.assertEqual(gdelt.category_for("175"), "conflict")
        self.assertEqual(gdelt.category_for("172"), "politics")
        self.assertIsNone(gdelt.category_for("040"), "cooperation is not a conflict event")

    def test_candidates_keep_the_best_covered_root_events_spread_across_countries(self):
        rows = [
            row("https://a.example/1", articles=50), row("https://a.example/1", articles=80),  # same URL: best kept
            row("https://a.example/2", articles=40), row("https://a.example/3", articles=30),
            row("https://b.example/1", articles=20, country="US"),
            row("https://skip.example/old", day="20260101"), row("https://skip.example/thin", articles=3),
            row("https://skip.example/country", geo_type="1"), row("https://skip.example/coop", code="040"),
            row("https://skip.example/notroot", root="0"), row("https://skip.example/nogeo", lat=""),
            ["short", "row"],
        ]
        picked = gdelt.candidates(rows, "20261004", per_country=2, limit=10)
        self.assertEqual([r[gdelt.SOURCE_URL] for r in picked], ["https://a.example/1", "https://a.example/2", "https://b.example/1"])
        self.assertEqual(picked[0][gdelt.NUM_ARTICLES], "80")
        self.assertEqual(len(gdelt.candidates(rows, "20261004", limit=1)), 1)

    def test_page_text_prefers_open_graph_and_strips_site_names(self):
        head = '<meta property="og:title" content="Strikes hit Gaza &amp; Rafah"><meta name="description" content="Overnight raids.">'
        self.assertEqual(gdelt.page_text(head), {"title": "Strikes hit Gaza & Rafah", "description": "Overnight raids."})
        plain = "<title>\n Police arrest five in Lyon | Example News </title>"
        self.assertEqual(gdelt.page_text(plain), {"title": "Police arrest five in Lyon", "description": None})
        self.assertEqual(gdelt.page_text("<meta charset='utf-8'>")["title"], None)
        radio = '<meta property="og:title" content="Banned books hit a record | Talk Radio 98.3 WLAC">'
        self.assertEqual(gdelt.page_text(radio)["title"], "Banned books hit a record")
        dated = '<meta property="og:title" content="2025-26 budget - what changes">'
        self.assertEqual(gdelt.page_text(dated)["title"], "2025-26 budget - what changes", "dashes in og titles are kept")

    def test_daily_rows_match_the_bundled_csv_shape_and_drop_pages_without_headlines(self):
        rows = [row("https://a.example/1", articles=90), row("https://a.example/2", articles=60)]
        texts = {"https://a.example/1": {"title": "Clashes in Gaza", "description": "Details."}, "https://a.example/2": None}
        with patch.object(gdelt, "fetch_day", return_value=rows):
            day, out = gdelt.daily_rows("20261005", fetch_text=texts.get)
        self.assertEqual(day, "20261005")
        self.assertEqual(len(out), 1)
        prepared = prepare_conflict_csv_row(out[0])
        self.assertEqual(prepared["event_id"], "conflict-csv:https://a.example/1")
        self.assertEqual((prepared["category"], prepared["title"], prepared["weight"]), ("conflict", "Clashes in Gaza", 90))
        self.assertEqual((prepared["lat"], prepared["lng"]), (31.5, 34.4))


if __name__ == "__main__":
    unittest.main()
