"""Offline tests for the CPU-first and no-full-source-download helpers."""
import unittest
from unittest.mock import patch

from clipper.pipeline.config import Config
from clipper.pipeline.remote_source import _headers_arg, extract_audio_window, extract_video_window


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

    def test_audio_window_rejects_invalid_source_without_network(self):
        with self.assertRaises(ValueError):
            extract_audio_window({"media_url": "file:///secret"}, 0, 10, "tmp.wav")

    def test_video_window_rejects_invalid_duration_without_network(self):
        with self.assertRaises(ValueError):
            extract_video_window({"media_url": "https://example.com/a.mp4"}, 0, 0.1, "tmp.mp4")

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
    def test_video_window_invokes_ffmpeg_for_selected_range_only(self, run):
        source = {"media_url": "https://media.example/video"}
        extract_video_window(source, 300, 25, "temp/clip.mp4", ffmpeg="ffmpeg")
        cmd = run.call_args.args[0]
        self.assertIn("300.000", cmd)
        self.assertIn("25.000", cmd)
        self.assertIn("0:v:0", cmd)
        self.assertIn("copy", cmd)


if __name__ == "__main__":
    unittest.main()
