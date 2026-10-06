"""News enrichment: placement on the globe, prompt shape, geocode queries, and the outlet list."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from jobs.news import enrich as e  # noqa: E402
from jobs.news import geocode, sources  # noqa: E402

ITEM = {"item_id": "abc", "publisher_name": "BBC News", "title": "Election called", "summary": "S"}
CENTROIDS = {"FRA": (46.0, 2.0)}


class EnrichTests(unittest.TestCase):
    def test_geocoded_places_win_then_the_country_centre(self):
        result = {"aboutCountryIso3": "fra", "locationSpecificity": "city"}
        placed = e.placement(result, {"lat": 48.85, "lng": 2.35}, CENTROIDS)
        self.assertEqual((placed["lat"], placed["geo_precision"], placed["geo_source"], placed["country_iso3"]),
                         (48.85, "city", "openstreetmap", "FRA"))
        fallback = e.placement(result, None, CENTROIDS)
        self.assertEqual((fallback["lat"], fallback["lng"], fallback["geo_precision"], fallback["geo_source"]),
                         (46.0, 2.0, "country", "centroid"))

    def test_stories_with_no_single_place_are_rejected(self):
        result = {"aboutCountryIso3": "", "locationSpecificity": "global"}
        self.assertIsNone(e.placement(result, None, CENTROIDS))
        self.assertIsNone(e.placement({"aboutCountryIso3": "ZZZ"}, None, CENTROIDS), "unknown country code")
        verdict = e.verdict_for(result, None)
        self.assertEqual((verdict["placed"], verdict["aboutCountryIso3"]), (False, None))
        self.assertEqual(verdict["reason"], "no single place to show on the globe")
        self.assertIsNone(e.verdict_for({"aboutCountryIso3": "FRA"}, {"lat": 0})["reason"])

    def test_prompt_carries_ids_outlet_and_only_current_layers(self):
        prompt = e.build_prompt([ITEM])
        items = json.loads(prompt.split("ITEMS:\n", 1)[1])
        self.assertEqual(items[0]["id"], "abc")
        self.assertEqual(items[0]["outlet"], "BBC News")
        self.assertIn("politics", prompt)
        self.assertNotIn("earthquake", prompt)

    def test_geocode_query_is_as_precise_as_the_specificity(self):
        place = {"country": "France", "region": "Île-de-France", "city": "Paris"}
        self.assertEqual(geocode.build_query(place, "city"), "Paris, France")
        self.assertEqual(geocode.build_query(place, "region"), "Île-de-France, France")
        self.assertEqual(geocode.build_query(place, "country"), "France")
        self.assertEqual(geocode.build_query({"country": "France"}, "city"), "France")
        self.assertEqual(geocode.build_query(place, "global"), "")

    def test_outlets_are_the_three_world_desks(self):
        self.assertEqual(sources.NEWS_SOURCES, ("bbc", "guardian", "aljazeera"))
        self.assertEqual(sources.SOURCE_BY_PUBLISHER["pub:theguardian.com"], "guardian")
        self.assertTrue(all(outlet["feed_url"].startswith("https://") for outlet in sources.OUTLETS))


if __name__ == "__main__":
    unittest.main()
