"""Config loading tests (offline)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.config import ConfigError, load_config

HERE = Path(__file__).resolve().parent
GOOD = HERE / "fixtures" / "config-good.yaml"
BAD_ENGINE = HERE / "fixtures" / "config-bad-engine.yaml"


class TestConfig(unittest.TestCase):
    def test_load_good(self):
        cfg = load_config(GOOD)
        self.assertEqual(cfg.tts.engine, "vieneu")
        self.assertEqual(cfg.tts.voice, "Hải Đăng")
        self.assertEqual(cfg.tts.precision, "fp32")
        self.assertEqual(cfg.youtube.privacy_status, "unlisted")
        self.assertEqual(cfg.youtube.latency, "low")
        self.assertEqual(cfg.obs.port, 4455)
        self.assertEqual(
            cfg.compliance.disclosure_overlay,
            "Nội dung AI - Avatar AI đang nói",
        )

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(HERE / "fixtures" / "nope.yaml")

    def test_bad_engine(self):
        with self.assertRaises(ConfigError):
            load_config(BAD_ENGINE)

    def test_obs_password_from_env(self):
        import os

        os.environ["OBS_WS_PASSWORD"] = "secret-test"
        try:
            cfg = load_config(GOOD)
            self.assertEqual(cfg.obs_password, "secret-test")
        finally:
            del os.environ["OBS_WS_PASSWORD"]


if __name__ == "__main__":
    unittest.main()
