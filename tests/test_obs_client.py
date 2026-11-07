"""OBS client tests (offline, fake client + request-shape checks)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.obs_client import FakeOBSClient, OBSClient, OBSError


class FakeReqClient:
    """Stand-in for obsws_python.ReqClient. Records calls."""

    def __init__(self, host, port, password):
        self.calls = []
        self.host = host
        self.port = port

    def get_scene_list(self):
        self.calls.append(("get_scene_list",))
        return type(
            "Resp", (), {"scenes": [{"sceneName": "Live"}]}
        )()

    def set_current_program_scene(self, name):
        self.calls.append(("set_current_program_scene", name))

    def start_stream(self):
        self.calls.append(("start_stream",))

    def stop_stream(self):
        self.calls.append(("stop_stream",))

    def set_input_settings(self, name, settings, overlay=False):
        self.calls.append(("set_input_settings", name, settings, overlay))

    def disconnect(self):
        self.calls.append(("disconnect",))


class TestOBSClient(unittest.TestCase):
    def test_fake_client_records(self):
        obs = FakeOBSClient()
        obs.set_scene("Live")
        obs.set_overlay_text("AI Disclosure", "Nội dung AI")
        obs.start_stream()
        self.assertEqual(obs.scenes, ["Live"])
        self.assertEqual(obs.overlay["AI Disclosure"], "Nội dung AI")
        self.assertTrue(obs.streaming)
        obs.stop_stream()
        self.assertFalse(obs.streaming)

    def test_real_client_calls_right_requests(self):
        client = OBSClient("localhost", 4455, "pw")
        fake = FakeReqClient("localhost", 4455, "pw")
        client._client = fake
        self.assertEqual(client.get_scene_list(), ["Live"])
        client.set_scene("Human")
        client.set_overlay_text("AI Disclosure", "Nội dung AI")
        self.assertIn(("set_current_program_scene", "Human"), fake.calls)
        self.assertIn(
            (
                "set_input_settings",
                "AI Disclosure",
                {"text": "Nội dung AI"},
                True,
            ),
            fake.calls,
        )

    def test_connect_failure_is_clear(self):
        client = OBSClient("127.0.0.1", 1, "")
        with self.assertRaises(OBSError):
            client.get_scene_list()


if __name__ == "__main__":
    unittest.main()
