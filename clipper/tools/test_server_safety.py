"""Regression tests for the local dashboard's request and filesystem boundaries.

Run from the clipper directory:
    python -m unittest discover -s tools -p "test_server_safety.py" -v
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

from pipeline import server as server_module
from pipeline.config import Config
from pipeline import posting
from pipeline.server import Handler, State


class ServerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.temp.name)
        self.state = State.__new__(State)
        self.state.library_root = self.root

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_video_inside_library(self):
        path = os.path.join(self.root, "stream.mp4")
        with open(path, "wb") as stream:
            stream.write(b"video")
        self.assertTrue(self.state.valid_vod(path))

    def test_rejects_non_video_inside_library(self):
        path = os.path.join(self.root, "notes.txt")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("not a video")
        self.assertFalse(self.state.valid_vod(path))

    def test_rejects_video_outside_library(self):
        with tempfile.TemporaryDirectory() as outside:
            path = os.path.join(outside, "outside.mp4")
            with open(path, "wb") as stream:
                stream.write(b"video")
            self.assertFalse(self.state.valid_vod(path))

    def test_accepts_project_folder_inside_library(self):
        folder = os.path.join(self.root, "stream - clips")
        os.makedirs(folder)
        with open(os.path.join(folder, "project.json"), "w", encoding="utf-8") as stream:
            stream.write("{}")
        self.assertTrue(self.state.valid_project_dir(folder))

    def test_rejects_project_folder_outside_library(self):
        with tempfile.TemporaryDirectory() as outside:
            with open(os.path.join(outside, "project.json"), "w", encoding="utf-8") as stream:
                stream.write("{}")
            self.assertFalse(self.state.valid_project_dir(outside))

    def test_rejects_library_root_as_project_folder(self):
        with open(os.path.join(self.root, "project.json"), "w", encoding="utf-8") as stream:
            stream.write("{}")
        self.assertFalse(self.state.valid_project_dir(self.root))

    def test_rejects_symlink_to_video_outside_library(self):
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, "outside.mp4")
            with open(target, "wb") as stream:
                stream.write(b"video")
            link = os.path.join(self.root, "linked.mp4")
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this runner")
            self.assertFalse(self.state.valid_vod(link))

    def _handler(self, host, origin=None):
        handler = Handler.__new__(Handler)
        handler.headers = {"Host": host}
        if origin is not None:
            handler.headers["Origin"] = origin
        handler.server = SimpleNamespace(server_port=8765)
        return handler

    def test_accepts_loopback_host(self):
        self.assertTrue(self._handler("127.0.0.1:8765")._local_request_allowed())
        self.assertTrue(self._handler("localhost:8765", "http://localhost:8765")._local_request_allowed())

    def test_rejects_remote_host_and_cross_origin(self):
        self.assertFalse(self._handler("attacker.example:8765")._local_request_allowed())
        self.assertFalse(self._handler("127.0.0.1:8765", "http://attacker.example:8765")._local_request_allowed())

    def test_rejects_wrong_port(self):
        self.assertFalse(self._handler("127.0.0.1:9999")._local_request_allowed())

    def test_paid_ai_is_disabled_by_default(self):
        self.assertFalse(Config().ai_enabled)

    def test_paid_ai_routes_fail_closed_when_disabled(self):
        previous = server_module.STATE
        server_module.STATE = SimpleNamespace(cfg=SimpleNamespace(ai_enabled=False))
        handler = Handler.__new__(Handler)
        handler._json = lambda payload, code=200: (payload, code)
        try:
            for route in (
                lambda: handler._ai("clip1", {}),
                lambda: handler._ai_titles(),
                lambda: handler._clean_captions("clip1"),
            ):
                payload, code = route()
                self.assertEqual(code, 403)
                self.assertIn("disabled", payload["error"])
        finally:
            server_module.STATE = previous

    def test_daily_upload_usage_counts_only_success_once(self):
        previous_path = posting.STORE_PATH
        try:
            posting.STORE_PATH = os.path.join(self.root, "posting.json")
            posting._save({
                "youtube": {}, "tiktok": {}, "seq": 2,
                "queue": [
                    {"id": "up1", "platform": "youtube", "status": "uploading"},
                    {"id": "up2", "platform": "tiktok", "status": "uploading"},
                ],
            })
            poster = posting.Poster(Config())
            poster._set("up1", status="uploaded", result={"video_id": "abc"})
            poster._set("up1", status="uploaded", result={"video_id": "abc"})
            poster._set("up2", status="uploaded", result={"publish_id": "xyz"})
            usage = poster.state()["usage"]
            self.assertEqual(usage["youtube_uploads"], 1)
            self.assertEqual(usage["tiktok_uploads"], 1)
        finally:
            posting.STORE_PATH = previous_path


if __name__ == "__main__":
    unittest.main()
