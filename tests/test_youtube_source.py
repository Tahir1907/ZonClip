"""Offline tests for YouTube URL normalization."""
import unittest

from clipper.pipeline.youtube_source import parse_youtube_url

VIDEO = "dQw4w9WgXcQ"


class YouTubeUrlTests(unittest.TestCase):
    def test_watch_and_extra_parameters(self):
        got = parse_youtube_url(f"https://www.youtube.com/watch?v={VIDEO}&t=42&list=PL123")
        self.assertEqual(got["video_id"], VIDEO)
        self.assertEqual(got["kind"], "watch")

    def test_short_link(self):
        self.assertEqual(parse_youtube_url(f"https://youtu.be/{VIDEO}?si=abc")["video_id"], VIDEO)

    def test_shorts_live_embed_and_v(self):
        for path in ("shorts", "live", "embed", "v"):
            with self.subTest(path=path):
                self.assertEqual(parse_youtube_url(f"https://youtube.com/{path}/{VIDEO}")["video_id"], VIDEO)

    def test_playlist_requires_video_id(self):
        got = parse_youtube_url(f"https://youtube.com/playlist?list=PL123&v={VIDEO}")
        self.assertEqual(got["kind"], "playlist_video")
        with self.assertRaises(ValueError):
            parse_youtube_url("https://youtube.com/playlist?list=PL123")

    def test_rejects_other_hosts_and_bad_ids(self):
        for url in ("https://example.com/watch?v=" + VIDEO,
                    "https://youtube.com/watch?v=bad",
                    "https://youtube.com/channel/UC123"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    parse_youtube_url(url)


if __name__ == "__main__":
    unittest.main()
