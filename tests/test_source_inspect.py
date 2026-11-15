"""Tests proving URL inspection requests metadata only and never invokes a download."""
import sys
import types
import unittest
from unittest.mock import patch

from clipper.pipeline.source_inspect import inspect_source_url


class FakeYoutubeDL:
    last_options = None
    def __init__(self, options):
        type(self).last_options = options
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def extract_info(self, url, download=False):
        self.download_argument = download
        return {
            "title": "Test VOD", "channel": "Test creator", "duration": 120,
            "live_status": "not_live", "subtitles": {"en": []},
            "automatic_captions": {}, "availability": "public",
        }


class MetadataOnlyTests(unittest.TestCase):
    def setUp(self):
        self.fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        self.patch = patch.dict(sys.modules, {"yt_dlp": self.fake_module})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_youtube_metadata_check_does_not_download(self):
        result = inspect_source_url("https://youtu.be/dQw4w9WgXcQ", "youtube")
        self.assertFalse(result["media_downloaded"])
        self.assertFalse(result["full_source_saved"])
        self.assertTrue(result["has_captions"])
        self.assertTrue(FakeYoutubeDL.last_options["skip_download"])
        self.assertTrue(FakeYoutubeDL.last_options["noplaylist"])

    def test_rejects_invalid_platform(self):
        with self.assertRaises(ValueError):
            inspect_source_url("https://example.com/video", "youtube")

    def test_rejects_playlist_only_link(self):
        with self.assertRaises(ValueError):
            inspect_source_url("https://youtube.com/playlist?list=PL123456", "youtube")


if __name__ == "__main__":
    unittest.main()
