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


class ModelReplyTests(unittest.TestCase):
    def test_scores_accept_the_shapes_gemini_sends(self):
        for value, expected in ((0.85, 0.85), ("0.85", 0.85), ("85%", 0.85), (85, 0.85), ("high", 0.8),
                                (" Medium ", 0.6), (1, 1.0), (1.4, 1.0), (-0.2, 0.0), ("n/a", 0.0),
                                (None, 0.0), (True, 0.0), (float("nan"), 0.0), ([0.9], 0.0)):
            self.assertAlmostEqual(links._score(value), expected, msg=repr(value))

    def test_string_plausibility_no_longer_crashes_and_strings_are_read(self):
        from unittest.mock import patch
        news_a = story("a", ["x"])
        candidates = [story("b", ["x"]), story("c", ["x"])]
        hypotheses = [{"article_id": "b", "plausibility": "0.9", "type": "SAME_EVENT", "causal_chain": ["a", "b"]},
                      {"article_id": "c", "plausibility": "low", "type": "SHARES_CONTEXT"}, "not a dict"]
        verification = {"accept": True, "confidence_overall": "95%", "confidence_plausibility": "high",
                        "confidence_evidence": 0.9, "reasoning_chain": "Same vote.", "explicit_evidence": "Both name it."}
        with patch.object(links, "_generate_hypotheses_batch", return_value=hypotheses), \
                patch.object(links, "_verify_hypothesis", return_value=verification) as verify, \
                patch.object(links.time, "sleep"):
            verified, calls = links._verify_news_news_batch(object(), news_a, candidates)
        self.assertEqual(calls, 1, "only the plausible hypothesis is verified")
        verify.assert_called_once()
        self.assertEqual(verified[0]["confidence"], 0.95)
        self.assertEqual(verified[0]["confidence_plausibility"], 0.8)
        self.assertEqual(verified[0]["evidence"], ["Both name it."])
        self.assertTrue(links._has_evidence(verified[0]), "article evidence alone is enough (no world knowledge)")
        self.assertFalse(links._has_evidence({"evidence": [], "world_knowledge": ""}))

    def test_one_failing_story_is_skipped_and_the_run_continues(self):
        from unittest.mock import MagicMock, patch
        conn = MagicMock()
        cursor = conn.cursor.return_value
        now = NOW
        cursor.fetchall.return_value = [
            ("a", "politics", "A", None, now, 0.0, 0.0, [{"text": "vote"}, {"text": "senate"}], True),
            ("b", "politics", "B", None, now, 0.0, 0.0, [{"text": "vote"}, {"text": "senate"}], True),
            ("c", "politics", "C", None, now, 0.0, 0.0, [{"text": "vote"}, {"text": "senate"}], True),
        ]
        outcomes = iter([TypeError("'>=' not supported between instances of 'str' and 'float'"), (2, 1, 0)])
        def link_one(*_args):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(links, "_link_one", side_effect=link_one):
            result = links.link_news_to_news(conn, client=object(), max_calls=50)
        self.assertEqual(result["links_created"], 1, "the second story still ran")
        conn.rollback.assert_called_once()
        marked = [call.args[1][0] for call in cursor.execute.call_args_list if "linked_at" in call.args[0]]
        self.assertIn(["a"], marked, "the failed story is marked so it is not retried every day")
