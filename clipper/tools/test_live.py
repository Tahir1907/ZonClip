"""Unit tests for the live recording watcher and ranking tiers (stdlib only)."""
import tempfile
import unittest
from pathlib import Path

from live import dedupe_highlights, discover_ready, tier_for_rank


class LiveWatcherTests(unittest.TestCase):
    def test_only_processes_after_file_is_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "segment_001.mkv"
            video.write_bytes(b"recording")
            state = {"observed": {}}
            self.assertEqual(discover_ready(root, state, 10, now=100), [])
            self.assertEqual(discover_ready(root, state, 10, now=109), [])
            self.assertEqual(discover_ready(root, state, 10, now=110), [video])

    def test_size_change_restarts_stability_window(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "segment_001.mp4"
            video.write_bytes(b"first")
            state = {"observed": {}}
            discover_ready(root, state, 5, now=100)
            video.write_bytes(b"larger")
            self.assertEqual(discover_ready(root, state, 5, now=110), [])
            self.assertEqual(discover_ready(root, state, 5, now=114), [])
            self.assertEqual(discover_ready(root, state, 5, now=115), [video])

    def test_ignores_temporary_and_non_video_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "segment.mp4.tmp").write_bytes(b"partial")
            (root / "notes.txt").write_text("ignore")
            self.assertEqual(discover_ready(root, {"observed": {}}, 0, now=100), [])

    def test_dynamic_quality_tiers_do_not_invent_clips(self):
        self.assertEqual(tier_for_rank(1, 1), "top")
        self.assertEqual(tier_for_rank(2, 5), "good")
        self.assertEqual(tier_for_rank(5, 5), "ranking")
        self.assertEqual(tier_for_rank(1, 0), "ranking")

    def test_deduplicates_same_moment_across_adjacent_segments(self):
        clips = [
            {"id": "segment-a:clip1", "score": 7, "global_start": 590, "global_end": 630},
            {"id": "segment-b:clip1", "score": 9, "global_start": 600, "global_end": 640},
            {"id": "segment-b:clip2", "score": 6, "global_start": 710, "global_end": 740},
        ]
        result = dedupe_highlights(clips)
        self.assertEqual([x["id"] for x in result], ["segment-b:clip1", "segment-b:clip2"])


if __name__ == "__main__":
    unittest.main()
