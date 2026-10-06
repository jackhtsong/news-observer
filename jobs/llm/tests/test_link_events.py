"""Linker candidate selection: only new stories, each pair once, best candidates first."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from jobs.llm import link_events as links  # noqa: E402

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def story(event_id, keywords, hours_ago=0, lat=0.0, lng=0.0, category="politics"):
    return {"id": event_id, "category": category, "occurred_at": NOW - timedelta(hours=hours_ago),
            "lat": lat, "lng": lng, "keywords": [{"text": word} for word in keywords]}


class CandidateTests(unittest.TestCase):
    def test_only_pending_stories_are_analysed_and_pairs_are_not_repeated(self):
        events = [story("new1", ["vote", "senate"]), story("new2", ["vote", "senate"]), story("old", ["vote", "senate"])]
        found = links._find_news_news_candidates(events, {"new1", "new2"})
        self.assertEqual(sorted(found), ["new1", "new2"])
        self.assertEqual([e["id"] for e in found["new1"]], ["new2", "old"])
        self.assertEqual([e["id"] for e in found["new2"]], ["old"], "new1↔new2 is already covered from new1")

    def test_candidates_are_capped_and_ranked_by_overlap_then_recency(self):
        events = [story("new", ["a", "b", "c"], category="finance")]
        events += [story(f"far{i}", ["x"], hours_ago=i, lat=60, category="finance") for i in range(1, 9)]
        events.append(story("match", ["a", "b", "c"], hours_ago=50, lat=60, category="sports"))
        found = links._find_news_news_candidates(events, {"new"}, max_candidates=3)["new"]
        self.assertEqual([e["id"] for e in found], ["match", "far1", "far2"])

    def test_unrelated_stories_yield_no_candidates(self):
        events = [story("new", ["a"], category="sports"), story("other", ["z"], hours_ago=100, lat=50, category="health")]
        self.assertEqual(links._find_news_news_candidates(events, {"new"}), {})


if __name__ == "__main__":
    unittest.main()
