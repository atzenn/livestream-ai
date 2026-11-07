"""YouTube Live API body-builder tests (offline, no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.youtube_live import (
    YouTubeError,
    YouTubeLiveClient,
    build_bind_params,
    build_broadcast_body,
    build_stream_body,
    build_synthetic_media_body,
    build_transition_params,
)


class TestYouTubeBodies(unittest.TestCase):
    def test_broadcast_body(self):
        body = build_broadcast_body(
            "Live test",
            "Buổi live thử nghiệm, nội dung AI.",
            "2026-11-13T12:30:00Z",
            privacy_status="unlisted",
        )
        self.assertEqual(body["snippet"]["title"], "Live test")
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")
        self.assertEqual(
            body["snippet"]["scheduledStartTime"], "2026-11-13T12:30:00Z"
        )

    def test_stream_body(self):
        body = build_stream_body("Live test")
        self.assertEqual(body["cdn"]["ingestionType"], "rtmp")
        self.assertEqual(body["cdn"]["resolution"], "1080p")
        self.assertEqual(body["cdn"]["frameRate"], "30fps")

    def test_synthetic_media_body(self):
        body = build_synthetic_media_body("abc123")
        self.assertEqual(body["id"], "abc123")
        self.assertTrue(body["status"]["containsSyntheticMedia"])

    def test_bind_params(self):
        p = build_bind_params("b1", "s1")
        self.assertEqual(p["id"], "b1")
        self.assertEqual(p["streamId"], "s1")

    def test_transition_params(self):
        p = build_transition_params("b1", "live")
        self.assertEqual(p["broadcastStatus"], "live")
        with self.assertRaises(YouTubeError):
            build_transition_params("b1", "bogus")

    def test_client_without_secrets_raises_clear_error(self):
        client = YouTubeLiveClient("", "")
        with self.assertRaises(YouTubeError) as ctx:
            client.create_broadcast("t", "d", "2026-11-13T12:30:00Z")
        self.assertIn("client_secrets_path", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
