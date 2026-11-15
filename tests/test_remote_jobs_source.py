"""Offline tests for YouTube selected-range job helpers."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from clipper.pipeline.remote_jobs import _concat_escape, _safe_title, _select_candidates
from clipper.pipeline.config import Config
from clipper.pipeline.transcribe import _clipwords_key


class RemoteJobHelperTests(unittest.TestCase):
    def test_candidate_selection_uses_score_order_and_spacing(self):
        high = SimpleNamespace(start=10, end=30, peak=18, score=90)
        overlapping = SimpleNamespace(start=25, end=45, peak=33, score=85)
        separate = SimpleNamespace(start=100, end=120, peak=110, score=80)
        got = _select_candidates([high, overlapping, separate], 3, min_gap_s=10)
        self.assertEqual(got, [high, separate])

    def test_candidate_selection_respects_requested_count(self):
        items = [SimpleNamespace(start=i * 100, end=i * 100 + 20, peak=i * 100 + 10, score=90-i)
                 for i in range(5)]
        self.assertEqual(len(_select_candidates(items, 2)), 2)

    def test_title_sanitizer_removes_path_characters(self):
        self.assertEqual(_safe_title("../../ GTA 6: Stream? ", "fallback"), "GTA 6 Stream")
        self.assertEqual(_safe_title("///", "video-id"), "video-id")

    def test_concat_path_uses_forward_slashes(self):
        escaped = _concat_escape(os.path.join("folder", "clip.mp4"))
        self.assertIn("/", escaped)
        self.assertNotIn("\\", escaped)

    def test_caption_cache_key_is_source_specific(self):
        cfg = Config()
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "one.wav")
            second = os.path.join(tmp, "two.wav")
            with open(first, "wb") as fh:
                fh.write(b"first audio")
            with open(second, "wb") as fh:
                fh.write(b"different audio")
            self.assertNotEqual(_clipwords_key(cfg, 10, 20, first),
                                _clipwords_key(cfg, 10, 20, second))


if __name__ == "__main__":
    unittest.main()
