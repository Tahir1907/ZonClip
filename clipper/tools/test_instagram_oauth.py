"""Regression tests for Instagram OAuth setup (network calls are not made)."""
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from pipeline import posting
from pipeline.config import Config


class InstagramOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_store = posting.STORE_PATH
        posting.STORE_PATH = os.path.join(self.temp.name, "posting.json")
        self.poster = posting.Poster(Config())

    def tearDown(self):
        posting.STORE_PATH = self.old_store
        self.temp.cleanup()

    def test_instagram_store_defaults_and_state(self):
        state = self.poster.state()
        self.assertIn("instagram", state)
        self.assertFalse(state["instagram"]["connected"])
        self.assertFalse(state["instagram"]["configured"])

    def test_instagram_auth_url_requires_app_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "Meta App ID"):
            self.poster.instagram_auth_url()

    def test_instagram_auth_url_uses_official_permissions_and_loopback_redirect(self):
        self.poster.save_creds("instagram", {"app_id": "app123", "app_secret": "secret"})
        url = self.poster.instagram_auth_url()
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "www.facebook.com")
        self.assertEqual(query["client_id"], ["app123"])
        self.assertIn("instagram_content_publish", query["scope"][0])
        self.assertEqual(query["redirect_uri"], [self.poster._redirect("instagram")])
        self.assertTrue(query["state"][0] in self.poster._pending_ig)

    def test_callback_rejects_unknown_state_before_network(self):
        with self.assertRaisesRegex(RuntimeError, "state did not match"):
            self.poster.instagram_callback("fake-code", "not-a-valid-state")

    def test_disconnect_keeps_meta_app_credentials_only(self):
        self.poster.save_creds("instagram", {"app_id": "app123", "app_secret": "secret"})
        with posting._LOCK:
            data = posting._load()
            data["instagram"].update({"page_access_token": "token", "ig_user_id": "123"})
            posting._save(data)
        self.poster.disconnect("instagram")
        state = self.poster.state()["instagram"]
        self.assertTrue(state["configured"])
        self.assertFalse(state["connected"])


if __name__ == "__main__":
    unittest.main()
