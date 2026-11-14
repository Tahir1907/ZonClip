"""Regression tests for the local dashboard's request and filesystem boundaries.

Run from the clipper directory:
    python -m unittest discover -s tools -p "test_server_safety.py" -v
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
