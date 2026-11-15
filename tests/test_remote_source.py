"""Offline tests for the CPU-first and no-full-source-video-download helpers."""
import sys
import types
import unittest
from unittest.mock import patch

from clipper.pipeline.config import Config
from clipper.pipeline.remote_source import (
    _headers_arg, _media_tracks, extract_audio_track, extract_audio_window,
    extract_video_window,
)


class LaptopProfileTests(unittest.TestCase):
    def test_defaults_fit_i7_4770hq_class_cpu(self):
        cfg = Config()
        self.assertEqual(cfg.device, "cpu")
        self.assertEqual(cfg.model, "base.en")
        self.assertEqual(cfg.compute_type_cpu, "int8")
        self.assertEqual(cfg.cpu_threads, 4)
        self.assertEqual(cfg.beam_size, 1)
        self.assertEqual((cfg.out_w, cfg.out_h, cfg.fps), (720, 1280, 30))
        self.assertFalse(cfg.use_nvenc)

    def test_header_block_is_crlf_and_filters_injection(self):
        got = _headers_arg({"User-Agent": "test-agent", "X-Bad": "ok\r\nInjected: yes"})
        self.assertEqual(got, "User-Agent: test-agent\r\n")

    def test_selects_progressive_stream(self):
        got = _media_tracks({"url": "https://media.example/a.mp4", "vcodec": "avc1",
                             "acodec": "mp4a", "format_id": "18"})
        self.assertEqual(got["media_url"], "https://media.example/a.mp4")

    def test_selects_single_progressive_requested_format(self):
        got = _media_tracks({"requested_formats": [
            {"url": "https://media.example/progressive", "vcodec": "avc1", "acodec": "mp4a",
             "format_id": "18"}
        ]})
        self.assertEqual(got["media_url"], "https://media.example/progressive")
        self.assertNotIn("video_url", got)

    def test_selects_separate_adaptive_streams(self):
        got = _media_tracks({"requested_formats": [
            {"url": "https://media.example/video", "vcodec": "avc1", "acodec": "none",
             "format_id": "137", "http_headers": {"User-Agent": "v"}},
            {"url": "https://media.example/audio", "vcodec": "none", "acodec": "mp4a",
             "format_id": "140", "http_headers": {"User-Agent": "a"}},
        ]})
        self.assertEqual(got["video_url"], "https://media.example/video")
        self.assertEqual(got["audio_url"], "https://media.example/audio")
        self.assertEqual(got["video_headers"]["User-Agent"], "v")
        self.assertEqual(got["audio_headers"]["User-Agent"], "a")

    def test_audio_window_rejects_invalid_source_without_network(self):
        with self.assertRaises(ValueError):
            extract_audio_window({"media_url": "file:///secret"}, 0, 10, "tmp.wav")

    def test_video_window_rejects_invalid_duration_without_network(self):
        with self.assertRaises(ValueError):
            extract_video_window({"media_url": "https://example.com/video"}, 0, 0.1, "tmp.mp4")

    @patch("clipper.pipeline.remote_source.subprocess.run")
    def test_audio_window_invokes_ffmpeg_for_only_requested_range(self, run):
        source = {"media_url": "https://media.example/video", "http_headers": {"User-Agent": "test"}}
        extract_audio_window(source, 120, 15, "temp/window.wav", ffmpeg="ffmpeg")
        cmd = run.call_args.args[0]
        self.assertIn("120.000", cmd)
        self.assertIn("15.000", cmd)
        self.assertIn("-vn", cmd)
        self.assertIn("16000", cmd)
        self.assertEqual(run.call_args.kwargs["timeout"], 240)

    @patch("clipper.pipeline.remote_source.subprocess.run")
    def test_audio_track_is_audio_only_and_uses_adaptive_audio_url(self, run):
        source = {"video_url": "https://media.example/video", "audio_url": "https://media.example/audio",
                  "audio_headers": {"User-Agent": "audio"}, "duration": 600}
        extract_audio_track(source, "temp/full-audio.wav", ffmpeg="ffmpeg")
        cmd = run.call_args.args[0]
        self.assertIn("https://media.example/audio", cmd)
        self.assertIn("-vn", cmd)
        self.assertNotIn("https://media.example/video", cmd)
        self.assertIn("16000", cmd)

    @patch("clipper.pipeline.remote_source.subprocess.run")
    def test_video_window_invokes_ffmpeg_for_selected_progressive_range(self, run):
        source = {"media_url": "https://media.example/video"}
        extract_video_window(source, 300, 25, "temp/clip.mp4", ffmpeg="ffmpeg")
        cmd = run.call_args.args[0]
        self.assertIn("300.000", cmd)
        self.assertIn("25.000", cmd)
        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertIn("copy", cmd)

    @patch("clipper.pipeline.remote_source.subprocess.run")
    def test_video_window_maps_separate_adaptive_audio_and_video(self, run):
        source = {"video_url": "https://media.example/video", "audio_url": "https://media.example/audio",
                  "video_headers": {"User-Agent": "video"}, "audio_headers": {"User-Agent": "audio"}}
        extract_video_window(source, 60, 20, "temp/clip.mp4", ffmpeg="ffmpeg")
        cmd = run.call_args.args[0]
        self.assertIn("https://media.example/video", cmd)
        self.assertIn("https://media.example/audio", cmd)
        self.assertIn("1:a:0?", cmd)
        self.assertEqual(cmd.count("60.000"), 2)


if __name__ == "__main__":
    unittest.main()
