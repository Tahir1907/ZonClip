"""Tests for VOD ranking group selection."""
import unittest

from pipeline.rankings import ranking_groups


class RankingGroupsTests(unittest.TestCase):
    def test_sorts_by_score_and_groups_three_per_video(self):
        clips = [
            {"id": "a", "rank": 1, "score": 2, "file": "a.mp4", "render_status": "done"},
            {"id": "b", "rank": 2, "score": 9, "file": "b.mp4", "render_status": "done"},
            {"id": "c", "rank": 3, "score": 5, "file": "c.mp4", "render_status": "done"},
            {"id": "d", "rank": 4, "score": 8, "file": "d.mp4", "render_status": "done"},
        ]
        groups = ranking_groups(clips, 3)
        self.assertEqual([[x["id"] for x in group] for group in groups],
                         [["b", "d", "c"], ["a"]])

    def test_ignores_unrendered_clips_and_keeps_partial_group(self):
        clips = [
            {"id": "done", "score": 1, "file": "done.mp4", "render_status": "done"},
            {"id": "failed", "score": 100, "file": "failed.mp4", "render_status": "error"},
        ]
        groups = ranking_groups(clips, 3)
        self.assertEqual([[x["id"] for x in group] for group in groups], [["done"]])

    def test_rejects_invalid_group_size(self):
        with self.assertRaises(ValueError):
            ranking_groups([], 0)


if __name__ == "__main__":
    unittest.main()
